"""
Paid game join — v1.6.0 Unified WebSocket EIP-712 flow.
Per paid-games.md & api-summary.md: connect WS → hello (paid) → sign_required → sign_submit → joined.
"""
import json
import websockets
from bot.web3.eip712_signer import sign_join_paid
from bot.credentials import get_agent_private_key
from bot.config import PAID_ENTRY_FEE_SMOLTZ, SKILL_VERSION
from bot.utils.logger import get_logger

log = get_logger(__name__)


async def join_paid_game(api_key: str):
    """
    Join a paid room via EIP-712 signed WebSocket flow.
    Returns (response_data, websocket) when successfully joined.
    """
    agent_pk = get_agent_private_key()
    if not agent_pk:
        raise RuntimeError("Agent private key not found")

    uri = "wss://cdn.moltyroyale.com/ws/join"
    headers = {
        "X-API-Key": api_key,
        "X-Version": SKILL_VERSION  # Wajib 1.6.0
    }

    log.info("Connecting to Unified Join WebSocket for PAID room...")
    
    try:
        # Step 1: Buka koneksi WebSocket
        async with websockets.connect(uri, extra_headers=headers) as ws:
            
            # Step 2: Tunggu frame 'welcome'
            welcome_msg = await ws.recv()
            welcome_data = json.loads(welcome_msg)
            
            if welcome_data.get("type") == "welcome":
                
                # Step 3: Kirim frame 'hello' khusus paid room
                hello_payload = {
                    "type": "hello",
                    "entryType": "paid",
                    "mode": "offchain" # Ubah ke "onchain" jika menggunakan Moltz on-chain
                }
                await ws.send(json.dumps(hello_payload))
                log.info("Sent 'hello' frame for paid room.")

                # Step 4: Handle State Machine dari server
                while True:
                    resp_msg = await ws.recv()
                    resp_data = json.loads(resp_msg)
                    msg_type = resp_data.get("type") or resp_data.get("status")

                    if msg_type == "sign_required":
                        log.info("Received sign_required. Signing EIP-712 data...")
                        
                        # Ekstrak joinIntentId dan data EIP-712 dari server
                        join_intent_id = resp_data.get("joinIntentId")
                        
                        # Lakukan proses signing menggunakan fungsi bawaanmu
                        # Sesuaikan ekstraksi 'resp_data' dengan struktur yang diminta oleh sign_join_paid
                        signature = sign_join_paid(agent_pk, resp_data) 
                        
                        # Step 5: Submit signature kembali ke server
                        sign_submit_payload = {
                            "type": "sign_submit",
                            "joinIntentId": join_intent_id,
                            "signature": signature
                        }
                        await ws.send(json.dumps(sign_submit_payload))
                        log.info("Signature submitted. Waiting for confirmation...")
                        
                    elif msg_type == "queued":
                        log.info("⏳ Queued for paid room...")
                        
                    elif msg_type == "tx_submitted":
                        log.info(f"Transaction submitted to chain. Hash: {resp_data.get('txHash')}")
                        
                    elif msg_type == "joined":
                        game_id = resp_data.get("gameId")
                        agent_id = resp_data.get("agentId")
                        log.info(f"✅ Successfully joined paid game: game={game_id} agent={agent_id}")
                        
                        # Kembalikan socket yang aktif agar bisa dilanjutkan oleh brain.py (game loop)
                        return resp_data, ws
                        
                    elif msg_type == "error":
                        log.error(f"❌ Error joining paid room: {resp_data}")
                        return None, None
                        
    except Exception as e:
        log.error(f"WebSocket error during paid matchmaking: {e}")
        return None, None
