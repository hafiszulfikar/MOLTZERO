import json
import websockets
from bot.config import SKILL_VERSION

async def join_free_game(api_key):
    uri = "wss://cdn.moltyroyale.com/ws/join"
    headers = {
        "X-API-Key": api_key,
        "X-Version": SKILL_VERSION  # Wajib 1.6.0
    }

    try:
        # 1. Buka koneksi ke Unified Join WS
        async with websockets.connect(uri, extra_headers=headers) as ws:
            
            # 2. Baca frame "welcome" dari server
            welcome_msg = await ws.recv()
            welcome_data = json.loads(welcome_msg)
            
            if welcome_data.get("type") == "welcome":
                # 3. Kirim frame "hello" untuk masuk antrean Free Room
                hello_payload = {
                    "type": "hello",
                    "entryType": "free"
                }
                await ws.send(json.dumps(hello_payload))

                # 4. Tunggu hasil antrean
                while True:
                    response_msg = await ws.recv()
                    response_data = json.loads(response_msg)
                    
                    if response_data.get("type") == "assigned" or response_data.get("status") == "assigned":
                        print(f"✅ Match Found! Game ID: {response_data.get('gameId')}")
                        # JANGAN TUTUP 'ws'. Return websocket ini ke game loop kamu!
                        return response_data, ws
                        
                    elif response_data.get("status") == "not_selected":
                        print("⚠️ Tidak dapat tempat di putaran ini. Harus re-dial...")
                        return None, None
                        
                    elif response_data.get("status") == "queued":
                        print("⏳ Masih mengantre di dalam server...")
                        
    except Exception as e:
        print(f"Error saat matchmaking: {e}")
        return None, None
