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
    meta = get_contract_info(chain, contract_info)
    contract = w3.eth.contract(address=meta["address"], abi=meta["abi"])

    head = w3.eth.get_block_number()
    start_block = max(head - 10, 0)
    end_block = head
    print(f"Scanning blocks {start_block} → {end_block} on {chain}")

    # Load warden key once
    try:
        with open(contract_info, "r") as _fh:
            _cfg = json.load(_fh)
        warden_key = _cfg.get("warden_private_key")
    except Exception as _ex:
        print(f"Failed to read warden key: {_ex}")
        return 0
    if not warden_key:
        print("warden_private_key missing in contract_info; aborting.")
        return 0

    if chain == "source":
        time.sleep(60)  
        dst_w3 = connect_to("destination")
        dst_meta = get_contract_info("destination", contract_info)
        dst_contract = dst_w3.eth.contract(address=dst_meta["address"], abi=dst_meta["abi"])

        try:
            deposit_events = contract.events.Deposit().get_logs(
                from_block=start_block, to_block=end_block
            )
            try:
                deposit_events.sort(key=lambda ev: (ev.blockNumber, ev.logIndex))
            except Exception:
                try:
                    deposit_events.sort(key=lambda ev: (ev["blockNumber"], ev["logIndex"]))
                except Exception:
                    pass
            print(f"Found {len(deposit_events)} Deposit event(s)")
        except Exception as ex:
            print(f"Error fetching Deposit logs: {ex}")
            deposit_events = []

        for i, ev in enumerate(deposit_events, 1):
            token = ev.args["token"]
            recipient = ev.args["recipient"]
            amount = ev.args["amount"]
            print(f"Deposit #{i}: token={token} recipient={recipient} amount={amount}")

            acct = dst_w3.eth.account.from_key(warden_key)
            nonce = dst_w3.eth.get_transaction_count(acct.address)
            fn = dst_contract.functions.wrap(token, recipient, amount)

            try:
                gas_est = fn.estimate_gas({"from": acct.address})
                gas_limit = int(gas_est * 1.2)
            except Exception:
                gas_limit = 200000

            tx = fn.build_transaction({
                "from": acct.address,
                "nonce": nonce,
                "gas": gas_limit,
                "gasPrice": dst_w3.eth.gas_price,
            })
            signed = dst_w3.eth.account.sign_transaction(tx, warden_key)
            tx_hash = dst_w3.eth.send_raw_transaction(signed.raw_transaction)
            print(f"Wrap sent: {tx_hash.hex()}")
            rcpt = dst_w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            print(f"Wrap confirmed in block {rcpt.blockNumber}")

            if i != len(deposit_events):
                time.sleep(1)

    elif chain == "destination":
        src_w3 = connect_to("source")
        src_meta = get_contract_info("source", contract_info)
        src_contract = src_w3.eth.contract(address=src_meta["address"], abi=src_meta["abi"])

        time.sleep(30)

        unwrap_events = []
        retries = 5
        print(f"Per-block scan for Unwrap events: {start_block}..{end_block}")
        for b in range(start_block, end_block + 1):
            fetched = False
            for attempt in range(1, retries + 1):
                try:
                    logs = contract.events.Unwrap().get_logs(from_block=b, to_block=b)
                    try:
                        logs.sort(key=lambda ev: (ev.blockNumber, ev["logIndex"]))
                    except Exception:
                        try:
                            logs.sort(key=lambda ev: (ev.blockNumber, ev.logIndex))
                        except Exception:
                            pass
                    unwrap_events.extend(logs)
                    print(f"Got Unwrap logs from block {b}")
                    fetched = True
                    break
                except Exception as ex:
                    backoff = min(2 ** (attempt - 1), 10)
                    print(f"Retry {attempt}/{retries} for block {b}: {ex}; sleeping {backoff}s")
                    time.sleep(backoff)
            if not fetched:
                print(f"Failed to fetch logs for block {b}")

        print(f"Found {len(unwrap_events)} Unwrap event(s)")
        for i, ev in enumerate(unwrap_events, 1):
            token = ev.args["underlying_token"]
            to_addr = ev.args["to"]
            amount = ev.args["amount"]
            print(f"Unwrap #{i}: token={token} to={to_addr} amount={amount}")

            acct = src_w3.eth.account.from_key(warden_key)
            nonce = src_w3.eth.get_transaction_count(acct.address)
            fn = src_contract.functions.withdraw(token, to_addr, amount)

            try:
                gas_est = fn.estimate_gas({"from": acct.address})
                gas_limit = int(gas_est * 1.2)
            except Exception:
                gas_limit = 200000

            tx = fn.build_transaction({
                "from": acct.address,
                "nonce": nonce,
                "gas": gas_limit,
                "gasPrice": src_w3.eth.gas_price,
            })
            signed = src_w3.eth.account.sign_transaction(tx, warden_key)
            tx_hash = src_w3.eth.send_raw_transaction(signed.raw_transaction)
            print(f"Withdraw sent: {tx_hash.hex()}")
            rcpt = src_w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            print(f"Withdraw confirmed in block {rcpt.blockNumber}")

            if i != len(unwrap_events):
                time.sleep(2)

    return 1

if __name__ == "__main__":
    scan_blocks("source")  
    time.sleep(10)
    scan_blocks("destination")

