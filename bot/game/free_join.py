"""
Free game join — v1.6.0 Unified WebSocket flow.
Connect WS → hello (free) → queued → assigned.
"""
import json
import websockets
from bot.config import SKILL_VERSION
from bot.utils.logger import get_logger

log = get_logger(__name__)

async def join_free_game(api):
    """
    Join a free room via Unified WebSocket flow.
    Returns (response_data, websocket) when successfully assigned.
    """
    # [PERBAIKAN KRUSIAL] Ambil API Key dari objek MoltyAPI jika yang dilempar adalah objek
    api_key_string = api.api_key if hasattr(api, 'api_key') else str(api)
    api_key_string = api_key_string.strip() # Bersihkan dari karakter enter/spasi yang tersembunyi

    uri = "wss://cdn.moltyroyale.com/ws/join"
    headers = {
        "Authorization": f"mr-auth {api_key_string}", # Format auth rekomendasi server
        "X-Version": SKILL_VERSION  # Wajib 1.6.0
    }

    log.info("Connecting to Unified Join WebSocket for FREE room...")

    try:
        async with websockets.connect(uri, additional_headers=headers) as ws:
            
            welcome_msg = await ws.recv()
            welcome_data = json.loads(welcome_msg)
            
            if welcome_data.get("type") == "welcome":
                
                hello_payload = {
                    "type": "hello",
                    "entryType": "free"
                }
                await ws.send(json.dumps(hello_payload))
                log.info("Sent 'hello' frame for free room.")

                while True:
                    response_msg = await ws.recv()
                    response_data = json.loads(response_msg)
                    msg_type = response_data.get("type") or response_data.get("status")
                    
                    if msg_type == "assigned":
                        game_id = response_data.get("gameId")
                        agent_id = response_data.get("agentId")
                        log.info(f"✅ Match Found! Game ID: {game_id} | Agent ID: {agent_id}")
                        return response_data, ws
                        
                    elif msg_type == "not_selected":
                        log.info("⚠️ Tidak dapat tempat di putaran ini. Harus re-dial...")
                        return {"status": "not_selected"}, None
                        
                    elif msg_type == "queued":
                        log.info("⏳ Masih mengantre di dalam server...")
            
            else:
                log.error(f"⚠️ Ditolak server sebelum masuk: {welcome_data}")
                return {"status": "error", "message": str(welcome_data)}, None

    except Exception as e:
        log.error(f"WebSocket error during free matchmaking: {e}")
        return {"status": "error", "message": str(e)}, None

    return {"status": "error", "message": "Fungsi free_join berhenti secara tidak terduga"}, None
