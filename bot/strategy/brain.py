"""
Strategy brain — main decision engine with priority-based action selection.
Implements the game-loop.md priority chain for high win rate.

v1.5.3 OP Mod changes:
- [OP MOD] Sniper + Hills Meta: Heavily prioritizes Hills if Sniper is equipped.
- [OP MOD] Absolute Kill Steal: +9000 priority for any target that can be 1-shot.
- [OP MOD] Weather Avoidance: Refuses to fight in Storm/Fog UNLESS it's a guaranteed 1-shot kill steal.
- [OP MOD] Time-to-Kill (TTK) combat calculation: fights only if guaranteed to win.
- [OP MOD] Anti-Gank protocol: flees if outnumbered in a single region.
- [OP MOD] Smart Healing: prevents overhealing and wasting high-tier meds.
"""
import math
from bot.utils.logger import get_logger

log = get_logger(__name__)

# ── Weapon stats from combat-items.md ─────────────────────────────────
WEAPONS = {
    "fist": {"bonus": 0, "range": 0},
    "dagger": {"bonus": 10, "range": 0},
    "sword": {"bonus": 20, "range": 0},
    "katana": {"bonus": 35, "range": 0},
    "bow": {"bonus": 5, "range": 1},
    "pistol": {"bonus": 10, "range": 1},
    "sniper": {"bonus": 28, "range": 2},
}

WEAPON_PRIORITY = ["katana", "sniper", "sword", "pistol", "dagger", "bow", "fist"]

# ── Item priority for pickup ──────────────────────────────────────────
ITEM_PRIORITY = {
    "rewards": 300,  # Moltz/sMoltz — ALWAYS pickup first
    "katana": 100, "sniper": 95, "sword": 90, "pistol": 85,
    "dagger": 80, "bow": 75,
    "medkit": 70, "bandage": 65, "emergency_food": 60, "energy_drink": 58,
    "binoculars": 55,  # Passive: vision +1 permanent, always pickup
    "map": 52,         # Use immediately to reveal entire map
    "megaphone": 40,
}

# ── Recovery items for healing (combat-items.md) ──────────────────────
RECOVERY_ITEMS = {
    "medkit": 50, "bandage": 30, "emergency_food": 20,
    "energy_drink": 0,  # EP restore, not HP
}

WEATHER_COMBAT_PENALTY = {
    "clear": 0.0,
    "rain": 0.05,   # -5%
    "fog": 0.10,    # -10%
    "storm": 0.15,  # -15%
}

def calc_damage(atk: int, weapon_bonus: int, target_def: int, weather: str = "clear") -> int:
    base = atk + weapon_bonus - int(target_def * 0.5)
    penalty = WEATHER_COMBAT_PENALTY.get(weather, 0.0)
    return max(1, int(base * (1 - penalty)))

def get_weapon_bonus(equipped_weapon) -> int:
    if not equipped_weapon: return 0
    type_id = equipped_weapon.get("typeId", "").lower()
    return WEAPONS.get(type_id, {}).get("bonus", 0)

def get_weapon_range(equipped_weapon) -> int:
    if not equipped_weapon: return 0
    type_id = equipped_weapon.get("typeId", "").lower()
    return WEAPONS.get(type_id, {}).get("range", 0)

_known_agents: dict = {}
_map_knowledge: dict = {"revealed": False, "death_zones": set(), "safe_center": []}

def _resolve_region(entry, view: dict):
    if isinstance(entry, dict): return entry
    if isinstance(entry, str):
        for r in view.get("visibleRegions", []):
            if isinstance(r, dict) and r.get("id") == entry:
                return r
    return None

def _get_region_id(entry) -> str:
    if isinstance(entry, str): return entry
    if isinstance(entry, dict): return entry.get("id", "")
    return ""

def reset_game_state():
    global _known_agents, _map_knowledge
    _known_agents = {}
    _map_knowledge = {"revealed": False, "death_zones": set(), "safe_center": []}
    log.info("Strategy brain reset for new game")

def decide_action(view: dict, can_act: bool, memory_temp: dict = None) -> dict | None:
    self_data = view.get("self", {})
    region = view.get("currentRegion", {})
    hp = self_data.get("hp", 100)
    max_hp = self_data.get("maxHp", 100)
    ep = self_data.get("ep", 10)
    max_ep = self_data.get("maxEp", 10)
    atk = self_data.get("atk", 10)
    defense = self_data.get("def", 5)
    is_alive = self_data.get("isAlive", True)
    inventory = self_data.get("inventory", [])
    equipped = self_data.get("equippedWeapon")

    visible_agents = view.get("visibleAgents", [])
    visible_monsters = view.get("visibleMonsters", [])
    visible_items_raw = view.get("visibleItems", [])
    visible_items = []
    
    for entry in visible_items_raw:
        if not isinstance(entry, dict): continue
        inner = entry.get("item")
        if isinstance(inner, dict):
            inner["regionId"] = entry.get("regionId", "")
            visible_items.append(inner)
        elif entry.get("id"):
            visible_items.append(entry)

    visible_regions = view.get("visibleRegions", [])
    connected_regions = view.get("connectedRegions", [])
    pending_dz = view.get("pendingDeathzones", [])
    alive_count = view.get("aliveCount", 100)

    connections = connected_regions or region.get("connections", [])
    interactables = region.get("interactables", [])
    region_id = region.get("id", "")
    region_terrain = region.get("terrain", "").lower() if isinstance(region, dict) else ""
    region_weather = region.get("weather", "").lower() if isinstance(region, dict) else ""

    if not is_alive: return None

    # ── Danger map (DZ + pending DZ) ───────────────────
    danger_ids = set()
    for dz in pending_dz:
        if isinstance(dz, dict): danger_ids.add(dz.get("id", ""))
        elif isinstance(dz, str): danger_ids.add(dz)
    for conn in connections:
        resolved = _resolve_region(conn, view)
        if resolved and resolved.get("isDeathZone"):
            danger_ids.add(resolved.get("id", ""))

    _track_agents(visible_agents, self_data.get("id", ""), region_id)
    move_ep_cost = _get_move_ep_cost(region_terrain, region_weather)

    # ── 1. DEATHZONE ESCAPE ───────
    if region.get("isDeathZone", False):
        safe = _find_safe_region(connections, danger_ids, view)
        if safe and ep >= move_ep_cost:
            return {"action": "move", "data": {"regionId": safe}, "reason": f"ESCAPE: In DZ! HP={hp}"}

    # ── 1b. Pre-escape pending DZ ────────────────
    if region_id in danger_ids:
        safe = _find_safe_region(connections, danger_ids, view)
        if safe and ep >= move_ep_cost:
            return {"action": "move", "data": {"regionId": safe}, "reason": "PRE-ESCAPE"}

    # ── [OP MOD] 2a. Anti-Gank / Flee Protocol ─────────────
    enemies = [a for a in visible_agents if not a.get("isGuardian", False) and a.get("isAlive", True) and a.get("id") != self_data.get("id")]
    enemies_here = [e for e in enemies if e.get("regionId") == region_id]
    if len(enemies_here) >= 2 and hp < 70 and ep >= move_ep_cost:
        safe = _find_safe_region(connections, danger_ids, view)
        if safe:
            log.warning("⚠️ ANTI-GANK: Outnumbered by %d agents, repositioning!", len(enemies_here))
            return {"action": "move", "data": {"regionId": safe}, "reason": "ANTI-GANK: Tactical retreat"}

    # ── 2b. Guardian threat evasion ─────────────
    guardians_here = [a for a in visible_agents if a.get("isGuardian", False) and a.get("isAlive", True) and a.get("regionId") == region_id]
    if guardians_here and hp < 40 and ep >= move_ep_cost:
        safe = _find_safe_region(connections, danger_ids, view)
        if safe:
            return {"action": "move", "data": {"regionId": safe}, "reason": f"GUARDIAN FLEE: HP={hp}"}

    # ── FREE ACTIONS ─────────
    pickup_action = _check_pickup(visible_items, inventory, region_id)
    if pickup_action: return pickup_action

    equip_action = _check_equip(inventory, equipped)
    if equip_action: return equip_action

    util_action = _use_utility_item(inventory, hp, ep, alive_count)
    if util_action: return util_action

    if not can_act: return None

    # ── [OP MOD] 3. Smart Healing Management ─────────────────────────────
    missing_hp = max_hp - hp
    if missing_hp >= 20:
        heal = _find_smart_healing_item(inventory, missing_hp)
        if heal:
            return {"action": "use_item", "data": {"itemId": heal["id"]}, "reason": f"SMART HEAL: HP={hp}, using {heal.get('typeId')}"}

    # ── 4. EP recovery ──────
    if ep == 0:
        energy_drink = _find_energy_drink(inventory)
        if energy_drink:
            return {"action": "use_item", "data": {"itemId": energy_drink["id"]}, "reason": "EP RECOVERY"}

    # ── [OP MOD] 5. Combat Engine (Kill Steal & Weather Avoidance) ───────────────
    valid_targets = [a for a in visible_agents if a.get("isAlive", True) and a.get("id") != self_data.get("id")]
    bad_weather = region_weather in ["storm", "fog"]
    
    if valid_targets and ep >= 2:
        w_range = get_weapon_range(equipped)
        best_target = None
        best_ttk_diff = -999 

        for target in valid_targets:
            if not _is_in_range(target, region_id, w_range, connections):
                continue
                
            enemy_hp = target.get("hp", 100)
            enemy_def = target.get("def", 5)
            enemy_atk = target.get("atk", 10)
            
            my_dmg = calc_damage(atk, get_weapon_bonus(equipped), enemy_def, region_weather)
            enemy_dmg = calc_damage(enemy_atk, _estimate_enemy_weapon_bonus(target), defense, region_weather)
            
            my_ttk = math.ceil(enemy_hp / my_dmg) if my_dmg > 0 else 999
            enemy_ttk = math.ceil(hp / enemy_dmg) if enemy_dmg > 0 else 999
            
            ttk_advantage = enemy_ttk - my_ttk
            is_kill_steal = (my_ttk == 1)

            # OP Logic: Absolute priority for Kill Steals
            if is_kill_steal:
                ttk_advantage += 9000
            elif bad_weather:
                # Avoid normal fighting during bad weather to prevent debuff trades
                continue 
            
            if is_kill_steal or my_ttk < enemy_ttk:
                if target.get("isGuardian") and my_ttk <= 3:
                    ttk_advantage += 5 
                
                if ttk_advantage > best_ttk_diff:
                    best_ttk_diff = ttk_advantage
                    best_target = target

        if best_target:
            target_type = "agent" if not best_target.get("isGuardian") else "guardian"
            return {
                "action": "attack",
                "data": {"targetId": best_target["id"], "targetType": "agent"},
                "reason": f"OP COMBAT ({target_type}): KS={is_kill_steal}. Target HP={best_target.get('hp')}"
            }

    # ── 7. Monster farming ───────────────────────────────
    monsters = [m for m in visible_monsters if m.get("hp", 0) > 0]
    if monsters and ep >= 2 and not bad_weather: # Refuse to farm in bad weather
        target = _select_weakest(monsters)
        w_range = get_weapon_range(equipped)
        if _is_in_range(target, region_id, w_range, connections):
            return {"action": "attack", "data": {"targetId": target["id"], "targetType": "monster"}, "reason": "MONSTER FARM"}

    # ── 8. Facility interaction ──────────────────────────
    if interactables and ep >= 2 and not region.get("isDeathZone"):
        facility = _select_facility(interactables, hp, ep)
        if facility:
            return {"action": "interact", "data": {"interactableId": facility["id"]}, "reason": f"FACILITY: {facility.get('type')}"}

    # ── [OP MOD] 9. Strategic movement (Sniper Meta Included) ────────
    if ep >= move_ep_cost and connections:
        move_target = _choose_move_target(connections, danger_ids, region, visible_items, alive_count, equipped)
        if move_target:
            return {"action": "move", "data": {"regionId": move_target}, "reason": "EXPLORE: Moving to better position"}

    # ── 10. Rest ───────────────────────
    if ep < 6 and not enemies_here and not region.get("isDeathZone") and region_id not in danger_ids:
        return {"action": "rest", "data": {}, "reason": f"REST: EP={ep}/{max_ep}, area is safe"}

    return None

# ── Helper functions ──────────────────────────────────────────────────

def _get_move_ep_cost(terrain: str, weather: str) -> int:
    if terrain == "water": return 3
    if weather == "storm": return 3
    return 2

def _estimate_enemy_weapon_bonus(agent: dict) -> int:
    weapon = agent.get("equippedWeapon")
    if not weapon: return 0
    type_id = weapon.get("typeId", "").lower() if isinstance(weapon, dict) else ""
    return WEAPONS.get(type_id, {}).get("bonus", 0)

def _check_pickup(items: list, inventory: list, region_id: str) -> dict | None:
    if len(inventory) >= 10: return None
    local_items = [i for i in items if isinstance(i, dict) and i.get("regionId") == region_id]
    if not local_items:
        local_items = [i for i in items if isinstance(i, dict) and i.get("id")]
    if not local_items: return None

    heal_count = sum(1 for i in inventory if isinstance(i, dict) and i.get("typeId", "").lower() in RECOVERY_ITEMS)
    local_items.sort(key=lambda i: _pickup_score(i, inventory, heal_count), reverse=True)
    best = local_items[0]
    score = _pickup_score(best, inventory, heal_count)
    
    if score > 0:
        return {"action": "pickup", "data": {"itemId": best["id"]}, "reason": f"PICKUP: {best.get('typeId')}"}
    return None

def _pickup_score(item: dict, inventory: list, heal_count: int) -> int:
    type_id = item.get("typeId", "").lower()
    category = item.get("category", "").lower()

    if type_id == "rewards" or category == "currency": return 300

    if category == "weapon":
        bonus = WEAPONS.get(type_id, {}).get("bonus", 0)
        current_best = max([WEAPONS.get(i.get("typeId", "").lower(), {}).get("bonus", 0) for i in inventory if isinstance(i, dict) and i.get("category") == "weapon"] + [0])
        return 100 + bonus if bonus > current_best else 0

    if type_id == "binoculars":
        has_binos = any(isinstance(i, dict) and i.get("typeId", "").lower() == "binoculars" for i in inventory)
        return 55 if not has_binos else 0

    if type_id == "map": return 52

    if type_id in RECOVERY_ITEMS:
        return ITEM_PRIORITY.get(type_id, 0) + (10 if heal_count < 4 else 0)

    if type_id == "energy_drink": return 58
    return ITEM_PRIORITY.get(type_id, 0)

def _check_equip(inventory: list, equipped) -> dict | None:
    current_bonus = get_weapon_bonus(equipped) if equipped else 0
    best, best_bonus = None, current_bonus
    for item in inventory:
        if isinstance(item, dict) and item.get("category") == "weapon":
            bonus = WEAPONS.get(item.get("typeId", "").lower(), {}).get("bonus", 0)
            if bonus > best_bonus:
                best, best_bonus = item, bonus
    if best:
        return {"action": "equip", "data": {"itemId": best["id"]}, "reason": f"EQUIP: +{best_bonus} ATK"}
    return None

def _find_safe_region(connections, danger_ids: set, view: dict = None) -> str | None:
    safe_regions = []
    for conn in connections:
        rid = conn if isinstance(conn, str) else conn.get("id", "")
        is_dz = conn.get("isDeathZone", False) if isinstance(conn, dict) else False
        
        if rid and not is_dz and rid not in danger_ids:
            terrain = conn.get("terrain", "").lower() if isinstance(conn, dict) else "plains"
            score = {"hills": 3, "plains": 2, "ruins": 1, "forest": 0, "water": -2}.get(terrain, 0)
            safe_regions.append((rid, score))

    if safe_regions:
        safe_regions.sort(key=lambda x: x[1], reverse=True)
        return safe_regions[0][0]

    for conn in connections:
        rid = conn if isinstance(conn, str) else conn.get("id", "")
        is_dz = conn.get("isDeathZone", False) if isinstance(conn, dict) else False
        if rid and not is_dz: return rid
    return None

def _find_smart_healing_item(inventory: list, missing_hp: int) -> dict | None:
    heals = [i for i in inventory if isinstance(i, dict) and i.get("typeId", "").lower() in RECOVERY_ITEMS and RECOVERY_ITEMS[i.get("typeId").lower()] > 0]
    if not heals: return None

    heals.sort(key=lambda i: abs(missing_hp - RECOVERY_ITEMS[i.get("typeId").lower()]))
    if missing_hp >= 60:
        heals.sort(key=lambda i: RECOVERY_ITEMS[i.get("typeId").lower()], reverse=True)
        
    return heals[0]

def _find_energy_drink(inventory: list) -> dict | None:
    for i in inventory:
        if isinstance(i, dict) and i.get("typeId", "").lower() == "energy_drink": return i
    return None

def _select_weakest(targets: list) -> dict:
    return min(targets, key=lambda t: t.get("hp", 999))

def _is_in_range(target: dict, my_region: str, weapon_range: int, connections=None) -> bool:
    target_region = target.get("regionId", "")
    if not target_region or target_region == my_region: return True
    if weapon_range >= 1 and connections:
        adj_ids = set([c if isinstance(c, str) else c.get("id", "") for c in connections])
        if target_region in adj_ids: return True
    return False

def _select_facility(interactables: list, hp: int, ep: int) -> dict | None:
    for fac in interactables:
        if isinstance(fac, dict) and not fac.get("isUsed"):
            ftype = fac.get("type", "").lower()
            if ftype == "medical_facility" and hp < 80: return fac
            if ftype in ["supply_cache", "watchtower", "broadcast_station"]: return fac
    return None

def _track_agents(visible_agents: list, my_id: str, my_region: str):
    global _known_agents
    for agent in visible_agents:
        if isinstance(agent, dict) and agent.get("id") and agent.get("id") != my_id:
            _known_agents[agent["id"]] = {
                "hp": agent.get("hp", 100), "atk": agent.get("atk", 10),
                "isGuardian": agent.get("isGuardian", False),
                "equippedWeapon": agent.get("equippedWeapon"),
                "lastSeen": my_region, "isAlive": agent.get("isAlive", True),
            }
    if len(_known_agents) > 50:
        dead = [k for k, v in _known_agents.items() if not v.get("isAlive", True)]
        for d in dead: del _known_agents[d]

def _use_utility_item(inventory: list, hp: int, ep: int, alive_count: int) -> dict | None:
    for item in inventory:
        if isinstance(item, dict) and item.get("typeId", "").lower() == "map":
            return {"action": "use_item", "data": {"itemId": item["id"]}, "reason": "UTILITY: Using Map"}
    return None

def learn_from_map(view: dict):
    global _map_knowledge
    visible_regions = view.get("visibleRegions", [])
    if not visible_regions: return

    _map_knowledge["revealed"] = True
    safe_regions = []

    for region in visible_regions:
        if not isinstance(region, dict): continue
        rid = region.get("id", "")
        if not rid: continue

        if region.get("isDeathZone"):
            _map_knowledge["death_zones"].add(rid)
        else:
            conns = region.get("connections", [])
            terrain = region.get("terrain", "").lower()
            terrain_value = {"hills": 3, "plains": 2, "ruins": 2, "forest": 1, "water": -1}.get(terrain, 0)
            score = len(conns) + terrain_value
            safe_regions.append((rid, score))

    safe_regions.sort(key=lambda x: x[1], reverse=True)
    _map_knowledge["safe_center"] = [r[0] for r in safe_regions[:5]]

def _choose_move_target(connections, danger_ids: set, current_region: dict, visible_items: list, alive_count: int, equipped: dict = None) -> str | None:
    candidates = []
    item_regions = set([i.get("regionId", "") for i in visible_items if isinstance(i, dict)])
    is_sniper = equipped and isinstance(equipped, dict) and equipped.get("typeId", "").lower() == "sniper"

    for conn in connections:
        if isinstance(conn, str):
            if conn in danger_ids: continue
            score = 6 if conn in item_regions else 1
            candidates.append((conn, score))
        elif isinstance(conn, dict):
            rid = conn.get("id", "")
            if not rid or conn.get("isDeathZone") or rid in danger_ids: continue

            terrain = conn.get("terrain", "").lower()
            score = {"hills": 4, "plains": 2, "ruins": 2, "forest": 1, "water": -3}.get(terrain, 0)
            
            # [OP MOD] Sniper + Hills Meta Priority
            if is_sniper and terrain == "hills":
                score += 15 # Massive boost for high ground if we have a sniper
            
            if rid in item_regions: score += 5
            
            facs = conn.get("interactables", [])
            if facs: score += len([f for f in facs if isinstance(f, dict) and not f.get("isUsed")]) * 2
            
            score += {"storm": -2, "fog": -1, "rain": 0, "clear": 1}.get(conn.get("weather", "").lower(), 0)
            if alive_count < 30: score += 3
            if _map_knowledge.get("revealed") and rid in _map_knowledge.get("safe_center", []): score += 5
            if rid in _map_knowledge.get("death_zones", set()): continue

            candidates.append((rid, score))

    if not candidates: return None
    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[0][0]
