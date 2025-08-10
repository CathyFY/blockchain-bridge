from web3 import Web3
from web3.providers.rpc import HTTPProvider
from web3.middleware import ExtraDataToPOAMiddleware #Necessary for POA chains
from datetime import datetime
import json
import pandas as pd
import time


def connect_to(chain):
    if chain == 'source':  # The source contract chain is avax
        # api_url = f"https://api.avax-test.network/ext/bc/C/rpc" #AVAX C-chain testnet
        api_url = f"https://avalanche-fuji.core.chainstack.com/ext/bc/C/rpc/951f69f30af92f3ce68d1b00ddc31e7d"
    if chain == 'destination':  # The destination contract chain is bsc
        # api_url = f"https://data-seed-prebsc-1-s1.binance.org:8545/" #BSC testnet
        api_url = f"https://bsc-testnet.core.chainstack.com/2d32c1491e2991be02b5a2ecba2c50be"

    if chain in ['source','destination']:
        w3 = Web3(Web3.HTTPProvider(api_url))
        # inject the poa compatibility middleware to the innermost layer
        w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    return w3


def get_contract_info(chain, contract_info):
    """
        Load the contract_info file into a dictionary
        This function is used by the autograder and will likely be useful to you
    """
    try:
        with open(contract_info, 'r')  as f:
            contracts = json.load(f)
    except Exception as e:
        print( f"Failed to read contract info\nPlease contact your instructor\n{e}" )
        return 0
    return contracts[chain]



def scan_blocks(chain, contract_info="contract_info.json"):
    """
        chain - (string) should be either "source" or "destination"
        Scan the last 5 blocks of the source and destination chains
        Look for 'Deposit' events on the source chain and 'Unwrap' events on the destination chain
        When Deposit events are found on the source chain, call the 'wrap' function the destination chain
        When Unwrap events are found on the destination chain, call the 'withdraw' function on the source chain
    """

    # This is different from Bridge IV where chain was "avax" or "bsc"
    if chain not in ['source','destination']:
        print( f"Invalid chain: {chain}" )
        return 0
    
        #YOUR CODE HERE
    w3 = connect_to(chain)
    contract_data = get_contract_info(chain, contract_info)
    contract = w3.eth.contract(address=contract_data['address'], abi=contract_data['abi'])

    latest_block = w3.eth.block_number
    start_block = max(0, latest_block - 5)  
    end_block = latest_block

    print(f"Scanning blocks {start_block} to {end_block} on {chain} chain")

    with open(contract_info, 'r') as f:
        full_cfg = json.load(f)
    warden_key = full_cfg["warden_private_key"]

    if chain == 'source':
        # Relay Deposits -> wrap() on destination
        time.sleep(3)
        dest_w3 = connect_to('destination')
        dest_data = get_contract_info('destination', contract_info)
        dest_contract = dest_w3.eth.contract(address=dest_data['address'], abi=dest_data['abi'])

        try:
            deposit_events = sorted(
                contract.events.Deposit().get_logs(from_block=start_block, to_block=end_block),
                key=lambda e: (e.blockNumber, e.logIndex)
            )
            print(f"Found {len(deposit_events)} Deposit event(s)")

            for i, event in enumerate(deposit_events, 1):
                token = event.args['token']
                recipient = event.args['recipient']
                amount = event.args['amount']
                print(f"→ Deposit #{i}: token={token}, recipient={recipient}, amount={amount}")

                warden = dest_w3.eth.account.from_key(warden_key)
                nonce = dest_w3.eth.get_transaction_count(warden.address)

                try:
                    gas_estimate = dest_contract.functions.wrap(token, recipient, amount).estimate_gas({'from': warden.address})
                    gas_limit = int(gas_estimate * 1.2)
                except Exception:
                    gas_limit = 200000

                tx = dest_contract.functions.wrap(token, recipient, amount).build_transaction({
                    'from': warden.address,
                    'nonce': nonce,
                    'gas': gas_limit,
                    'gasPrice': dest_w3.eth.gas_price
                })

                signed = dest_w3.eth.account.sign_transaction(tx, warden_key)
                tx_hash = dest_w3.eth.send_raw_transaction(signed.raw_transaction)
                print(f"✓ wrap tx sent: {tx_hash.hex()}")

        except Exception as e:
            print(f"Error processing deposit events: {e}")

    elif chain == 'destination':
        # Relay Unwraps -> withdraw() on source
        src_w3 = connect_to('source')
        src_data = get_contract_info('source', contract_info)
        src_contract = src_w3.eth.contract(address=src_data['address'], abi=src_data['abi'])

        time.sleep(2)

        unwrap_events = []
        max_retries = 3

        print(f"Scanning for Unwrap events from {start_block} to {end_block}, one block at a time")

        for b in range(start_block, end_block + 1):
            for attempt in range(1, max_retries + 1):
                try:
                    logs = sorted(
                        contract.events.Unwrap().get_logs(from_block=b, to_block=b),
                        key=lambda e: (e.blockNumber, e['logIndex'])
                    )
                    unwrap_events.extend(logs)
                    break
                except Exception as e:
                    print(f"Retry {attempt}/{max_retries} failed for block {b}: {e}")
                    time.sleep(min(2 ** (attempt - 1) + uniform(0.1, 0.5), 5))
            else:
                print(f"All retries failed for block {b}")

        print(f"Found {len(unwrap_events)} Unwrap event(s)")

        for i, event in enumerate(unwrap_events, 1):
            token = event.args['underlying_token']
            to = event.args['to']
            amount = event.args['amount']

            print(f"→ Unwrap #{i}: token={token}, to={to}, amount={amount}")
            warden = src_w3.eth.account.from_key(warden_key)
            nonce = src_w3.eth.get_transaction_count(warden.address)

            try:
                gas_estimate = src_contract.functions.withdraw(token, to, amount).estimate_gas({'from': warden.address})
                gas_limit = int(gas_estimate * 1.2)
            except Exception:
                gas_limit = 200000

            tx = src_contract.functions.withdraw(token, to, amount).build_transaction({
                'from': warden.address,
                'nonce': nonce,
                'gas': gas_limit,
                'gasPrice': src_w3.eth.gas_price
            })

            signed = src_w3.eth.account.sign_transaction(tx, warden_key)
            tx_hash = src_w3.eth.send_raw_transaction(signed.raw_transaction)
            print(f"✓ withdraw tx sent: {tx_hash.hex()}")

    return 1


if __name__ == "__main__":
    scan_blocks("source")
    time.sleep(2)
    scan_blocks("destination")

