"""
Paid game join — v1.6.0 Unified WebSocket EIP-712 flow.
Connect WS → hello (paid) → sign_required → sign_submit → joined.
"""
import json
import websockets
from bot.web3.eip712_signer import sign_join_paid
from bot.credentials import get_agent_private_key
from bot.config import SKILL_VERSION
from bot.utils.logger import get_logger

log = get_logger(__name__)

async def join_paid_game(api_key: str):
    """
    Join a paid room via EIP-712 signed WebSocket flow.
    Returns (response_data, websocket) when successfully joined.
    """
    agent_pk = get_agent_private_key()
    if not agent_pk:
        log.error("Agent private key not found")
        return {"status": "error", "message": "Agent private key not found"}, None

    uri = "wss://cdn.moltyroyale.com/ws/join"
    headers = {
        "Authorization": f"mr-auth {api_key}",  # <-- Gunakan format mr-auth
        "X-Version": SKILL_VERSION
    }

    log.info("Connecting to Unified Join WebSocket for PAID room...")
    
    try:
        # Menggunakan additional_headers untuk library websockets versi terbaru
        async with websockets.connect(uri, additional_headers=headers) as ws:
            
            welcome_msg = await ws.recv()
            welcome_data = json.loads(welcome_msg)
            
            if welcome_data.get("type") == "welcome":
                
                hello_payload = {
                    "type": "hello",
                    "entryType": "paid",
                    "mode": "offchain" 
                }
                await ws.send(json.dumps(hello_payload))
                log.info("Sent 'hello' frame for paid room.")

                while True:
                    resp_msg = await ws.recv()
                    resp_data = json.loads(resp_msg)
                    msg_type = resp_data.get("type") or resp_data.get("status")

                    if msg_type == "sign_required":
                        log.info("Received sign_required. Signing EIP-712 data...")
                        join_intent_id = resp_data.get("joinIntentId")
                        
                        signature = sign_join_paid(agent_pk, resp_data) 
                        
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
                        # Jangan tutup socket-nya, return ke game loop!
                        return resp_data, ws
                        
                    elif msg_type == "error":
                        log.error(f"❌ Error joining paid room: {resp_data}")
                        return {"status": "error", "message": str(resp_data)}, None
            
            else:
                log.error(f"⚠️ Ditolak server sebelum masuk (Paid): {welcome_data}")
                return {"status": "error", "message": str(welcome_data)}, None

    except Exception as e:
        log.error(f"WebSocket error during paid matchmaking: {e}")
        return {"status": "error", "message": str(e)}, None

    # Jaring pengaman mutlak
    return {"status": "error", "message": "Fungsi paid_join berhenti secara tidak terduga"}, None
