"""
redeem.py - Polymarket Gasless Redemption via Builder Relayer

PROXY WALLET FIX:
  py-builder-relayer-client v0.0.1 hardcodes TransactionType.SAFE in execute().
  We monkey-patch it to send type=PROXY for Magic Link proxy wallets.

CALLDATA FIX:
  Always use CTF contract directly with parentCollectionId=bytes32(0),
  indexSets=[1,2]. Do NOT use NegRiskAdapter for CLOB tokens.
"""

import os
import logging
from eth_abi import encode

logger = logging.getLogger(__name__)

CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
RELAYER_URL = "https://relayer-v2.polymarket.com/"
CHAIN_ID = 137
REDEEM_SELECTOR = bytes.fromhex("01a9a068")

_patched = False


def _patch_relay_client_for_proxy():
    global _patched
    if _patched:
        return

    from py_builder_relayer_client.client import RelayClient
    from py_builder_relayer_client.models import SafeTransactionArgs
    from py_builder_relayer_client.exceptions import RelayerClientException
    from py_builder_relayer_client.endpoints import SUBMIT_TRANSACTION
    from py_builder_relayer_client.http_helpers.helpers import POST
    from py_builder_relayer_client.response import ClientRelayerTransactionResponse
    from py_builder_relayer_client.builder.safe import build_safe_transaction_request

    def execute_proxy(self, transactions, metadata=None):
        self.assert_signer_needed()
        self.assert_builder_creds_needed()

        from_address = self.signer.address()

        nonce_payload = self.get_nonce(from_address, "PROXY")
        nonce = 0
        if nonce_payload is None or nonce_payload.get("nonce") is None:
            raise RelayerClientException("invalid nonce payload received")
        nonce = nonce_payload.get("nonce")

        safe_args = SafeTransactionArgs(
            from_address=from_address,
            nonce=nonce,
            chain_id=self.chain_id,
            transactions=transactions,
        )

        txn_request = build_safe_transaction_request(
            signer=self.signer,
            args=safe_args,
            config=self.contract_config,
            metadata=metadata,
        )

        txn_request.type = "PROXY"

        txn_request_dict = txn_request.to_dict()
        resp = self._post_request(POST, SUBMIT_TRANSACTION, txn_request_dict)
        return ClientRelayerTransactionResponse(
            resp.get("transactionID"),
            resp.get("transactionHash"),
            self,
        )

    RelayClient.execute = execute_proxy
    _patched = True
    logger.info("Patched RelayClient for PROXY wallet support")


def _build_redeem_data(condition_id):
    cond_bytes = bytes.fromhex(
        condition_id[2:] if condition_id.startswith("0x") else condition_id
    )
    params = encode(
        ["address", "bytes32", "bytes32", "uint256[]"],
        [USDC_ADDRESS, bytes(32), cond_bytes, [1, 2]],
    )
    return "0x" + (REDEEM_SELECTOR + params).hex()


async def redeem_position(
    private_key,
    funder_address,
    condition_id,
    token_id,
    size,
    neg_risk=False,
):
    result = {"success": False, "error": None, "tx_hash": None}

    try:
        _patch_relay_client_for_proxy()

        from py_builder_relayer_client.client import RelayClient
        from py_builder_relayer_client.models import SafeTransaction, OperationType
        from py_builder_signing_sdk.config import BuilderConfig
        from py_builder_signing_sdk.sdk_types import BuilderApiKeyCreds

        builder_key = os.getenv("BUILDER_API_KEY", "") or os.getenv("BUILDER_KEY", "")
        builder_secret = os.getenv("BUILDER_SECRET", "")
        builder_passphrase = os.getenv("BUILDER_PASSPHRASE", "")

        if not all([builder_key, builder_secret, builder_passphrase]):
            result["error"] = "Builder credentials not configured."
            return result

        builder_config = BuilderConfig(
            local_builder_creds=BuilderApiKeyCreds(
                key=builder_key,
                secret=builder_secret,
                passphrase=builder_passphrase,
            )
        )

        relay_client = RelayClient(
            RELAYER_URL,
            CHAIN_ID,
            private_key,
            builder_config,
        )

        calldata = _build_redeem_data(condition_id)
        logger.info(
            "Redeem: CTF direct PROXY mode, condition=%s..., size=%s",
            condition_id[:16], size,
        )

        tx = SafeTransaction(
            to=CTF_ADDRESS,
            operation=OperationType.Call,
            data=calldata,
            value="0",
        )

        logger.info("Redeem: submitting to relayer for %s...", funder_address[:10])
        response = relay_client.execute([tx], metadata="BetPoly: Redeem")

        if response:
            tx_result = response.wait()

            if tx_result:
                tx_hash = None
                if hasattr(tx_result, "transactionHash"):
                    tx_hash = tx_result.transactionHash
                elif hasattr(tx_result, "transaction_hash"):
                    tx_hash = tx_result.transaction_hash
                elif isinstance(tx_result, dict):
                    tx_hash = (
                        tx_result.get("transactionHash")
                        or tx_result.get("transaction_hash")
                        or tx_result.get("hash")
                    )
                elif isinstance(tx_result, str):
                    tx_hash = tx_result

                state = None
                if hasattr(tx_result, "state"):
                    state = tx_result.state
                elif isinstance(tx_result, dict):
                    state = tx_result.get("state")

                if state and "FAIL" in str(state).upper():
                    result["error"] = "Transaction failed on-chain: %s" % state
                    result["tx_hash"] = tx_hash
                    logger.error("Redeem FAILED: state=%s, tx=%s", state, tx_hash)
                else:
                    result["success"] = True
                    result["tx_hash"] = tx_hash
                    logger.info("Redeem SUCCESS: tx=%s", tx_hash)
            else:
                result["error"] = "Relayer returned empty confirmation."
                logger.error("Redeem: empty wait() result")
        else:
            result["error"] = "Relayer returned no response."
            logger.error("Redeem: execute() returned None")

    except ImportError as e:
        result["error"] = "Missing package: %s" % e
        logger.error("Redeem import error: %s", e)
    except Exception as e:
        error_msg = str(e)
        logger.error("Redeem FAILED: %s", error_msg)

        if "rate limit" in error_msg.lower():
            result["error"] = "Rate limited. Try again in a few minutes."
        elif "401" in error_msg or "403" in error_msg:
            result["error"] = "Builder authentication failed."
        elif "revert" in error_msg.lower():
            result["error"] = "Transaction reverted on-chain."
        else:
            result["error"] = "Redemption failed: %s" % error_msg[:200]

    return result
