"""
SMC_PMAX_RECOVERY — max 10-step no-loss pending engine.

Step 1: M1 valid BuyStop/SellStop outside zones; M5 PMAX+HalfTrend approach gate;
        on hit cancel opposite stops (do NOT glue reverse at SL); TP1→BE; TP2/TP3 widen-only.
Step 2A: M5 reverse + basket profit → close all, re-arm base stops.
Step 2B: M5 reverse + basket loss → open 2 reverse trades:
         1 cover (lot sized to recover ALL same-side market loss incl. manual within ~50 pips)
         + 1 runner (base lot, SL=0 until cover+priors close, then BE SL).
Step 3–10: reverse-signal only; 1 extra recovery trade per new reverse; shared TP/SL.
"""
import time
import MetaTrader5 as mt5
import config
from strategy import Strategy
from candlestick_patterns import h1_m5_pattern_gate

MAGIC_MAIN_BUY = 100988
MAGIC_MAIN_SELL = 100989
MAGIC_TP3_BUY = 100992
MAGIC_TP3_SELL = 100993
MAGIC_TP2_BUY = 100994
MAGIC_TP2_SELL = 100995
MAGIC_BREAKOUT_BUY = 100996
MAGIC_BREAKOUT_SELL = 100997
MAGIC_BRK_TP2_BUY = 100998
MAGIC_BRK_TP3_BUY = 100999
MAGIC_BRK_TP2_SELL = 100900
MAGIC_BRK_TP3_SELL = 100901
MAGIC_REC_BUY = 100902
MAGIC_REC_SELL = 100903
MAGIC_NEUT_BUY = 100904
MAGIC_NEUT_SELL = 100905

BUY_STOP_LEGS = (
    (MAGIC_BREAKOUT_BUY, MAGIC_MAIN_BUY),
    (MAGIC_BRK_TP2_BUY, MAGIC_TP2_BUY),
    (MAGIC_BRK_TP3_BUY, MAGIC_TP3_BUY),
)
SELL_STOP_LEGS = (
    (MAGIC_BREAKOUT_SELL, MAGIC_MAIN_SELL),
    (MAGIC_BRK_TP2_SELL, MAGIC_TP2_SELL),
    (MAGIC_BRK_TP3_SELL, MAGIC_TP3_SELL),
)
# Step2: index0 = cover (full recovery lot), index1 = runner (base lot)
REC_BUY_MAGICS = (MAGIC_REC_BUY, 100906)
REC_SELL_MAGICS = (MAGIC_REC_SELL, 100908)
REC_BUY_COVER_MAGIC, REC_BUY_RUNNER_MAGIC = REC_BUY_MAGICS
REC_SELL_COVER_MAGIC, REC_SELL_RUNNER_MAGIC = REC_SELL_MAGICS
NEUT_BUY_MAGICS = (MAGIC_NEUT_BUY, 100910, 100911)
NEUT_SELL_MAGICS = (MAGIC_NEUT_SELL, 100912, 100913)
MAGIC_TOPUP_SELL = (100914, 100915, 100916, 100917, 100918, 100919)
MAGIC_TOPUP_BUY = (100920, 100921, 100922, 100923, 100924, 100925)
MAGIC_EXTRA_BUY = 100926   # Step3+ single add-on buy
MAGIC_EXTRA_SELL = 100927  # Step3+ single add-on sell
MAGIC_MANUAL = 999999      # Dashboard manual market — isolated from engine
MAGIC_MANUAL_PENDING = 777888

ALL_ENGINE_MAGICS = set(
    [m for pair in BUY_STOP_LEGS for m in pair]
    + [m for pair in SELL_STOP_LEGS for m in pair]
    + list(REC_BUY_MAGICS) + list(REC_SELL_MAGICS)
    + [100907, 100909]  # legacy step2 3rd-leg magics (still recognize)
    + list(NEUT_BUY_MAGICS) + list(NEUT_SELL_MAGICS)
    + list(MAGIC_TOPUP_SELL) + list(MAGIC_TOPUP_BUY)
    + [MAGIC_EXTRA_BUY, MAGIC_EXTRA_SELL]
)
MANUAL_MAGICS = {MAGIC_MANUAL, MAGIC_MANUAL_PENDING}
REC_REVERSE_MAGICS = set(list(REC_BUY_MAGICS) + list(REC_SELL_MAGICS) + [100907, 100909])


MONEY_GENERATOR_MAGICS = set(range(500800, 501000))

def is_money_generator_magic(magic):
    return int(magic or 0) in MONEY_GENERATOR_MAGICS

def is_manual_magic(magic):
    return int(magic or 0) in MANUAL_MAGICS


def is_engine_magic(magic):
    return int(magic or 0) in ALL_ENGINE_MAGICS


def default_state():
    return {
        "step": 1,
        "phase": "FLAT",
        "origin": None,
        "prior_tickets": [],
        "reverse_tickets": [],
        "runner_ticket": None,
        "original_leg_lot": float(getattr(config, "PENDING_BASE_LOT", 0.02)),
        "buy_zone": None,
        "sell_zone": None,
        "hedge_opened": False,
        "neutralize_opened": False,
        "zero_loss_price": None,
        "be_exits_set": False,
        "topup_done": False,
        "cover_tickets": [],
        "last_log": "",
        "last_recovery_signal": None,
    }


def ensure_state(bot_state):
    st = bot_state.get("smc_recovery")
    if not isinstance(st, dict):
        st = default_state()
        bot_state["smc_recovery"] = st
    for k, v in default_state().items():
        st.setdefault(k, v)
    return st


def _digits():
    return 2 if "XAU" in str(config.SYMBOL).upper() else 5


def _cancel_orders(orders):
    for o in orders:
        try:
            mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": o.ticket})
        except Exception:
            pass


def _xau_stops(orders, buy=True):
    t = mt5.ORDER_TYPE_BUY_STOP if buy else mt5.ORDER_TYPE_SELL_STOP
    out = []
    for o in orders:
        if o.type != t:
            continue
        if "XAU" not in str(getattr(o, "symbol", "")).upper():
            continue
        out.append(o)
    return out


def _remove_sl(mt5_client, positions, add_log, tag="RECOVERY"):
    for p in positions:
        if float(getattr(p, "sl", 0) or 0) <= 0:
            continue
        if mt5_client.modify_sl(p.ticket, 0.0):
            add_log(f"🔓 {tag}: removed SL on #{p.ticket}")


def _remove_tp(mt5_client, positions, add_log, tag="RECOVERY"):
    for p in positions:
        if float(getattr(p, "tp", 0) or 0) <= 0:
            continue
        if mt5_client.modify_tp(p.ticket, 0.0):
            add_log(f"🔓 {tag}: removed TP on #{p.ticket}")


def _vol_sum(positions):
    return sum(float(getattr(p, "volume", 0) or 0) for p in positions)


def _avg_entry(positions):
    v = _vol_sum(positions)
    if v <= 0:
        return 0.0
    return sum(float(p.price_open) * float(p.volume) for p in positions) / v


def _zero_loss_price(priors, reverses, buffer_usd=0.0, money_per_point_per_lot=1.0):
    """
    Price P where prior PnL + reverse PnL ≈ +buffer_usd (covers spread/commission).
    Requires unequal volumes. buffer shifts P so flatten is slightly better than raw 0.
    """
    if not priors or not reverses:
        return None
    m = max(float(money_per_point_per_lot), 1e-9)
    buf = float(buffer_usd) / m  # convert $ buffer → price*volume units
    if priors[0].type == mt5.POSITION_TYPE_BUY and reverses[0].type == mt5.POSITION_TYPE_SELL:
        vb, eb = _vol_sum(priors), _avg_entry(priors)
        vs, es = _vol_sum(reverses), _avg_entry(reverses)
        if abs(vb - vs) < 1e-9:
            return None
        # (P-eb)*vb + (es-P)*vs = buf  →  P = (eb*vb - es*vs + buf) / (vb - vs)
        return (vb * eb - vs * es + buf) / (vb - vs)
    if priors[0].type == mt5.POSITION_TYPE_SELL and reverses[0].type == mt5.POSITION_TYPE_BUY:
        vs, es = _vol_sum(priors), _avg_entry(priors)
        vb, eb = _vol_sum(reverses), _avg_entry(reverses)
        if abs(vs - vb) < 1e-9:
            return None
        return (vs * es - vb * eb + buf) / (vs - vb)
    return None


def _select_cover_reverses(priors, reverses):
    """
    Exactly SMC_COVER_LEGS (default 1) reverse trade(s) cover zero-loss.
    Prefer designated cover magic, then largest volume; remaining = runner(s).
    """
    if not reverses:
        return []
    n_cover = int(getattr(config, "SMC_COVER_LEGS", 1))
    if len(reverses) <= n_cover:
        return list(reverses)
    cover_magics = {REC_BUY_COVER_MAGIC, REC_SELL_COVER_MAGIC, MAGIC_REC_BUY, MAGIC_REC_SELL}
    preferred = [p for p in reverses if int(getattr(p, "magic", 0) or 0) in cover_magics]
    pool = preferred if preferred else list(reverses)
    ordered = sorted(
        pool,
        key=lambda p: (float(p.volume), int(getattr(p, "ticket", 0) or 0)),
        reverse=True,
    )
    return ordered[:n_cover]


def _xau_side_positions(positions, side, exclude_magics=None):
    """All XAU positions on side (engine + manual/foreign), optionally excluding magics."""
    want = mt5.POSITION_TYPE_BUY if side == "BUY" else mt5.POSITION_TYPE_SELL
    excl = set(int(m) for m in (exclude_magics or []))
    out = []
    for p in positions:
        if "XAU" not in str(getattr(p, "symbol", "")).upper():
            continue
        if p.type != want:
            continue
        mag = int(getattr(p, "magic", 0) or 0)
        if mag not in ALL_ENGINE_MAGICS or mag in excl:
            continue
        out.append(p)
    return out


def _open_step2_hedge_pair(mt5_client, side, cover_lot, runner_lot, add_log):
    """Open exactly 2 reverse trades: 1 cover (recovery lot) + 1 runner (base lot)."""
    if side == "SELL":
        cover_magic, runner_magic = REC_SELL_COVER_MAGIC, REC_SELL_RUNNER_MAGIC
    else:
        cover_magic, runner_magic = REC_BUY_COVER_MAGIC, REC_BUY_RUNNER_MAGIC
    ok_c = _open_one(mt5_client, side, cover_lot, cover_magic, add_log)
    ok_r = _open_one(mt5_client, side, runner_lot, runner_magic, add_log)
    add_log(
        f"⚡ STEP2 OPEN: {side} cover lot={cover_lot} magic={cover_magic} ok={ok_c} | "
        f"runner lot={runner_lot} magic={runner_magic} ok={ok_r}"
    )
    return ok_c, ok_r


def _apply_hedge_zero_loss_exits(mt5_client, priors, reverses, add_log, m1_df=None, m5_df=None):
    """
    Step 2B:
    - Exactly 2 cover reverses: TP at buffered zero-loss P
    - Priors: SL at same P
    - Reverse side: NO SL on cover; runner: BE SL (when valid) + M1 zone TP (NOT P)
    """
    dig = _digits()
    if not priors or not reverses:
        return None, []
    prior_tag = "BUY" if priors[0].type == mt5.POSITION_TYPE_BUY else "SELL"
    rev_tag = "SELL" if reverses[0].type == mt5.POSITION_TYPE_SELL else "BUY"
    cover = _select_cover_reverses(priors, reverses)
    if not cover:
        return None, []
    mpl1 = mt5_client.money_per_lot_for_move(config.SYMBOL, 1.0)
    buf = float(getattr(config, "SMC_ZERO_LOSS_BUFFER_USD", 5.0))
    p = _zero_loss_price(priors, cover, buffer_usd=buf, money_per_point_per_lot=mpl1)
    if p is None or p <= 0:
        add_log("⚠️ ZERO-LOSS N/A — need 2 cover legs volume ≠ prior volume (top-up).")
        return None, cover
    p = round(float(p), dig)
    cover_ids = {p0.ticket for p0 in cover}
    runners = [r for r in reverses if r.ticket not in cover_ids]

    # Cover: no SL; clear old TPs on cover only, then set shared P
    _remove_sl(mt5_client, cover, add_log, tag=f"{rev_tag}-COVER-NO-SL")
    _remove_tp(mt5_client, cover, add_log, tag=f"{rev_tag}-COVER-CLEAR-TP")

    changed = False
    for pos in priors:
        cur_sl = float(getattr(pos, "sl", 0) or 0)
        if abs(cur_sl - p) > 0.05:
            if mt5_client.modify_sl(pos.ticket, p):
                add_log(f"🎯 ZERO-LOSS: {prior_tag} #{pos.ticket} SL → {p}")
                changed = True
    for pos in cover:
        cur_tp = float(getattr(pos, "tp", 0) or 0)
        if abs(cur_tp - p) > 0.05:
            if mt5_client.modify_tp(pos.ticket, p):
                add_log(f"🎯 ZERO-LOSS: {rev_tag} COVER #{pos.ticket} TP → {p} (vol={pos.volume})")
                changed = True

    # Runner(s): NEVER zero-loss TP; keep SL=0 during active hedge (BE only after cover done)
    for r in runners:
        _remove_sl(mt5_client, [r], add_log, tag="RUNNER-NO-SL-YET")

        # Set runner TP directly on M1 SMC zone boundary (far structural support/resistance)
        side = "SELL" if r.type == mt5.POSITION_TYPE_SELL else "BUY"
        zone_tp = None
        if m1_df is not None and m5_df is not None:
            _, _, tp2, tp3 = Strategy.calculate_pending_zone_sl_tp(m1_df, m5_df, side, float(r.price_open))
            zone_tp = round(float(tp3 or tp2 or 0.0), dig)
            if side == "SELL" and zone_tp >= p:
                zone_tp = round(p - 10.0, dig)
            elif side == "BUY" and zone_tp <= p:
                zone_tp = round(p + 10.0, dig)
        
        if not zone_tp or zone_tp <= 0:
            zone_tp = round(float(r.price_open) - 20.0 if side == "SELL" else float(r.price_open) + 20.0, dig)

        cur_tp = float(getattr(r, "tp", 0) or 0)
        if abs(cur_tp - zone_tp) > 0.3:
            if mt5_client.modify_tp(r.ticket, zone_tp):
                add_log(f"🏃 RUNNER SMC ZONE TP: {side} #{r.ticket} TP → {zone_tp} (M1 structural zone)")

    if changed:
        add_log(
            f"📐 STEP2 COVER: {len(cover)} {rev_tag} @ TP={p} (buf=${buf:.1f}); "
            f"runners={len(runners)} SL=0+zoneTP; prior {prior_tag} SL={p}"
        )
    elif runners:
        add_log(
            f"📐 STEP2 COVER OK: {len(cover)} {rev_tag} TP={p}; runners={len(runners)} zoneTP"
        )
    return p, cover


def _apply_full_flatten_exits(mt5_client, losing_side_pos, recover_side_pos, add_log):
    """
    Step 3 whipsaw: ALL recover TP + ALL lose SL at buffered zero-loss P → flat ~0+.
    """
    dig = _digits()
    if not losing_side_pos or not recover_side_pos:
        return None
    mpl1 = mt5_client.money_per_lot_for_move(config.SYMBOL, 1.0)
    buf = float(getattr(config, "SMC_ZERO_LOSS_BUFFER_USD", 5.0))
    if losing_side_pos[0].type == mt5.POSITION_TYPE_SELL and recover_side_pos[0].type == mt5.POSITION_TYPE_BUY:
        p = _zero_loss_price(losing_side_pos, recover_side_pos, buffer_usd=buf, money_per_point_per_lot=mpl1)
    elif losing_side_pos[0].type == mt5.POSITION_TYPE_BUY and recover_side_pos[0].type == mt5.POSITION_TYPE_SELL:
        p = _zero_loss_price(losing_side_pos, recover_side_pos, buffer_usd=buf, money_per_point_per_lot=mpl1)
    else:
        return None
    if p is None or p <= 0:
        add_log("⚠️ FULL-FLATTEN price N/A — need recover volume ≠ lose volume.")
        return None
    p = round(float(p), dig)
    _remove_tp(mt5_client, losing_side_pos, add_log, tag="FLATTEN")
    for pos in losing_side_pos:
        if mt5_client.modify_sl(pos.ticket, p):
            add_log(f"🎯 FLATTEN: lose-side #{pos.ticket} SL → {p}")
    _remove_sl(mt5_client, recover_side_pos, add_log, tag="FLATTEN")
    for pos in recover_side_pos:
        if mt5_client.modify_tp(pos.ticket, p):
            add_log(f"🎯 FLATTEN: recover-side #{pos.ticket} TP → {p}")
    return p


def _recovery_target_move(m1_atr=None):
    """Fixed ~50-pip recovery horizon (config SMC_RECOVERY_TARGET_MOVE)."""
    fixed = float(getattr(config, "SMC_RECOVERY_TARGET_MOVE", 5.0))
    floor = float(getattr(config, "SMC_RECOVERY_MOVE_FLOOR", 5.0))
    return max(fixed, floor)


def _mpl1(mt5_client):
    return mt5_client.money_per_lot_for_move(config.SYMBOL, 1.0)


def _projected_loss_at_price(positions, side, exit_price, mpl1):
    """USD loss if side closes at exit_price (0 if that price is profitable)."""
    total = 0.0
    ep = float(exit_price)
    for p in positions:
        o = float(p.price_open)
        v = float(p.volume)
        if side == "BUY":
            total += max(0.0, (o - ep) * v * mpl1)
        else:
            total += max(0.0, (ep - o) * v * mpl1)
    return total


def _calc_recovery_leg_lot(need_usd, target_move, money_per_lot_per_move, n_legs, vmin, vstep, vmax):
    """
    Per-leg lot so total n_legs covers need_usd within target_move.
    """
    need = max(float(need_usd), 0.0)
    legs = max(int(n_legs), 1)
    mpl = max(float(money_per_lot_per_move), 1e-9)
    raw = (need / legs) / mpl if need > 0 else 0.0
    if raw <= 0:
        return 0.0
    stepped = max(float(vmin), round(raw / float(vstep)) * float(vstep))
    return round(min(stepped, float(vmax)), 2)


def _open_recovery_legs(mt5_client, side, lot, m1_df, m5_df, price, magics, add_log):
    """Open recovery market legs with SL=0 and TP=0."""
    order_type = mt5.ORDER_TYPE_SELL if side == "SELL" else mt5.ORDER_TYPE_BUY
    opened = 0
    for magic in magics:
        res = mt5_client.open_order(config.SYMBOL, order_type, float(lot), 0.0, 0.0, magic=magic)
        if res:
            opened += 1
    add_log(f"⚡ RECOVERY OPEN: {opened}× {side} lot={lot}")
    return opened


def _open_one(mt5_client, side, lot, magic, add_log):
    order_type = mt5.ORDER_TYPE_SELL if side == "SELL" else mt5.ORDER_TYPE_BUY
    res = mt5_client.open_order(config.SYMBOL, order_type, float(lot), 0.0, 0.0, magic=magic)
    add_log(f"⚡ EXTRA OPEN: {side} lot={lot} magic={magic} ok={bool(res)}")
    return bool(res)


def _gate_log(st, add_log, msg):
    if st.get("_pat_gate_msg") != msg:
        st["_pat_gate_msg"] = msg
        add_log(msg)


def _place_both_base_stops(mt5_client, m1_df, m5_df, buy_trig, sell_trig, st, lvls, base_lot, add_log, buy_stops, sell_stops, bias=None):
    """Re-arm BOTH BuyStop + SellStop (SL=0), strictly outside zones."""
    dig = _digits()
    tick = mt5.symbol_info_tick(config.SYMBOL)
    ask = float(getattr(tick, "ask", buy_trig) or buy_trig)
    bid = float(getattr(tick, "bid", sell_trig) or sell_trig)
    for s in [config.SYMBOL + "m", "XAUUSDm", config.SYMBOL + "c", "XAUUSDc", config.SYMBOL]:
        t = mt5.symbol_info_tick(s)
        if t:
            ask, bid = float(t.ask), float(t.bid)
            break
    buy_trig, sell_trig = _ensure_stops_outside_zones(buy_trig, sell_trig, lvls, ask, bid, dig)
    _, b_tp1, b_tp2, b_tp3 = Strategy.calculate_pending_zone_sl_tp(
        m1_df, m5_df, "BUY", buy_trig, opposite_entry=sell_trig
    )
    _, s_tp1, s_tp2, s_tp3 = Strategy.calculate_pending_zone_sl_tp(
        m1_df, m5_df, "SELL", sell_trig, opposite_entry=buy_trig
    )
    sl0 = float(getattr(config, "SMC_PENDING_STOP_SL", 0.0))
    buy_legs = [
        (MAGIC_BREAKOUT_BUY, b_tp1),
        (MAGIC_BRK_TP2_BUY, b_tp2),
        (MAGIC_BRK_TP3_BUY, b_tp3),
    ]
    sell_legs = [
        (MAGIC_BREAKOUT_SELL, s_tp1),
        (MAGIC_BRK_TP2_SELL, s_tp2),
        (MAGIC_BRK_TP3_SELL, s_tp3),
    ]
    _sync_pending_outside(
        mt5_client, buy_stops, sell_stops, buy_trig, sell_trig, dig, add_log,
        freeze_buy=(str(bias).upper() == "BUY"), freeze_sell=(str(bias).upper() == "SELL"), lvls=lvls,
    )
    n1 = _fill_stops(mt5_client, buy_stops, buy_trig, sl0, buy_legs, "BUY_STOP", base_lot, add_log)
    n2 = _fill_stops(mt5_client, sell_stops, sell_trig, sl0, sell_legs, "SELL_STOP", base_lot, add_log)
    if n1 or n2:
        add_log(f"📌 BASE STOPS outside zone: buy@{buy_trig} sell@{sell_trig} lot={base_lot}")
    return n1 + n2


def _apply_step2_hedge_exits(mt5_client, priors, reverses, P, m1_df, m5_df, add_log):
    """
    Step2 hedge phase:
    - ALL same-side priors (engine + manual/magic-0): TP cleared, SL = P
      so every losing trade closes together when cover TP hits P
    - 1 cover reverse: TP = P, SL = 0
    - 1 runner: SL = 0 (NO breakeven yet), zone TP only
      BE SL only after cover+priors close (RUNNER phase).
    """
    dig = _digits()
    P = round(float(P), dig)
    cover = _select_cover_reverses(priors, reverses)
    if len(cover) < 1:
        return None, []
    cover_ids = {c.ticket for c in cover}
    runners = [r for r in reverses if r.ticket not in cover_ids]
    if not priors:
        return None, cover
    prior_tag = "BUY" if priors[0].type == mt5.POSITION_TYPE_BUY else "SELL"
    rev_tag = "SELL" if reverses[0].type == mt5.POSITION_TYPE_SELL else "BUY"

    # Cover + runner must stay SL-free while hedge is active
    _remove_sl(mt5_client, cover + runners, add_log, tag=f"{rev_tag}-NO-SL")

    # Sync EVERY prior (bot + manual) to same exit level as the 3 bot legs
    _remove_tp(mt5_client, priors, add_log, tag=f"{prior_tag}-PRIOR-CLEAR-TP")
    for pos in priors:
        mag = int(getattr(pos, "magic", 0) or 0)
        tag = "MANUAL" if (not is_engine_magic(mag)) else "ENGINE"
        cur_sl = float(getattr(pos, "sl", 0) or 0)
        if abs(cur_sl - P) > 0.05:
            if mt5_client.modify_sl(pos.ticket, P):
                add_log(f"🎯 STEP2: {tag} {prior_tag} #{pos.ticket} magic={mag} SL → {P}")
        cur_tp = float(getattr(pos, "tp", 0) or 0)
        if cur_tp > 0:
            if mt5_client.modify_tp(pos.ticket, 0.0):
                add_log(f"🔓 STEP2: {tag} {prior_tag} #{pos.ticket} TP cleared (exit only via SL@{P})")

    for pos in cover:
        if mt5_client.modify_tp(pos.ticket, P):
            add_log(f"🎯 STEP2: {rev_tag} COVER #{pos.ticket} TP → {P}")

    for r in runners:
        # Explicitly keep runner SL cleared every sync pass
        if float(getattr(r, "sl", 0) or 0) > 0:
            if mt5_client.modify_sl(r.ticket, 0.0):
                add_log(f"🔓 RUNNER KEEP OPEN: cleared premature SL #{r.ticket}")
        side = "SELL" if r.type == mt5.POSITION_TYPE_SELL else "BUY"
        _, tp1, _, _ = Strategy.calculate_pending_zone_sl_tp(m1_df, m5_df, side, float(r.price_open))
        tp1 = round(float(tp1), dig)
        if abs(tp1 - P) < 1.0:
            tp1 = round(P - 15.0, dig) if side == "SELL" else round(P + 15.0, dig)
        if mt5_client.modify_tp(r.ticket, tp1):
            add_log(f"🏃 RUNNER ZONE TP #{r.ticket} → {tp1} (SL=0 until cover done)")
    add_log(
        f"📐 STEP2 SET: P={P} priors={len(priors)} (engine+manual) "
        f"cover={len(cover)} runners={len(runners)} (runner SL deferred)"
    )
    return P, cover


def _pos_pnl(p):
    return float(getattr(p, "profit", 0) or 0) + float(getattr(p, "swap", 0) or 0)


def _basket_pnl(positions, side=None):
    total = 0.0
    for p in positions:
        if side == "BUY" and p.type != mt5.POSITION_TYPE_BUY:
            continue
        if side == "SELL" and p.type != mt5.POSITION_TYPE_SELL:
            continue
        total += _pos_pnl(p)
    return total


def _engine_positions(positions):
    """Only recovery-engine tickets. Manual / foreign magics are ignored completely."""
    return [p for p in positions if is_engine_magic(getattr(p, "magic", 0))]


def _split_engine_sides(positions):
    eng = _engine_positions(positions)
    buys = [p for p in eng if p.type == mt5.POSITION_TYPE_BUY]
    sells = [p for p in eng if p.type == mt5.POSITION_TYPE_SELL]
    return buys, sells


def _ensure_stops_outside_zones(
    buy_trig, sell_trig, lvls, curr_ask, curr_bid, dig,
    confirmed_buy=False, confirmed_sell=False, broker_stops=0.0
):
    """
    Hard rule: BuyStop ABOVE resistance zone high; SellStop BELOW support zone low.
    Never leave a trigger inside the zone body or close to live noise.
    """
    is_gold = "XAU" in str(config.SYMBOL).upper()
    buf = float(lvls.get("buffer") or 1.0)
    # Confirmed side should be fill-ready quickly (minimal safety distance),
    # unconfirmed side stays parked farther to avoid accidental hit.
    min_safe = max(float(broker_stops or 0.0), 0.3 if is_gold else 0.0003)
    buy_safe_buf = max(buf, min_safe) if confirmed_buy else max(buf, 10.0 if is_gold else 2.0)
    sell_safe_buf = max(buf, min_safe) if confirmed_sell else max(buf, 10.0 if is_gold else 2.0)

    res = lvls.get("res_zone") or (float(buy_trig) - 5.0, float(buy_trig))
    sup = lvls.get("sup_zone") or (float(sell_trig), float(sell_trig) + 5.0)
    rz0, rz1 = float(res[0]), float(res[1])
    if rz0 > rz1:
        rz0, rz1 = rz1, rz0
    sz0, sz1 = float(sup[0]), float(sup[1])
    if sz0 > sz1:
        sz0, sz1 = sz1, sz0

    buy = Strategy.price_outside_zone("BUY_STOP", rz0, rz1, buy_safe_buf)
    sell = Strategy.price_outside_zone("SELL_STOP", sz0, sz1, sell_safe_buf)

    # Must also be beyond live price by safe_buf for pending stops to avoid spike triggers
    buy = max(float(buy), float(curr_ask) + buy_safe_buf)
    sell = min(float(sell), float(curr_bid) - sell_safe_buf)

    # Re-assert outside zone after price push
    if not Strategy.assert_outside_zone(buy, rz0, rz1):
        buy = rz1 + buy_safe_buf
        if buy <= float(curr_ask):
            buy = float(curr_ask) + buy_safe_buf
            if buy <= rz1:
                buy = rz1 + buy_safe_buf
    if not Strategy.assert_outside_zone(sell, sz0, sz1):
        sell = sz0 - sell_safe_buf
        if sell >= float(curr_bid):
            sell = float(curr_bid) - sell_safe_buf
            if sell >= sz0:
                sell = sz0 - sell_safe_buf

    return round(buy, dig), round(sell, dig)


def _sync_pending_outside(mt5_client, buy_stops, sell_stops, buy_trig, sell_trig, dig, add_log,
                          freeze_buy=False, freeze_sell=False, lvls=None):
    """
    Move pending to outside-zone triggers.
    If freeze_*=True (PMAX+HalfTrend both agree on that side), only push if still INSIDE zone body.
    """
    res = (lvls or {}).get("res_zone")
    sup = (lvls or {}).get("sup_zone")

    def _inside_res(price):
        if not res:
            return False
        a, b = float(res[0]), float(res[1])
        lo, hi = (a, b) if a <= b else (b, a)
        return lo <= float(price) <= hi

    def _inside_sup(price):
        if not sup:
            return False
        a, b = float(sup[0]), float(sup[1])
        lo, hi = (a, b) if a <= b else (b, a)
        return lo <= float(price) <= hi

    for o in buy_stops:
        cur = float(o.price_open)
        if freeze_buy and not _inside_res(cur):
            continue
        if abs(cur - buy_trig) >= 0.3:
            if mt5_client.modify_pending_order(o.ticket, price=buy_trig, reason="zone_sync"):
                add_log(f"📐 OUTSIDE ZONE: BuyStop #{o.ticket} {cur} → {buy_trig}")
    for o in sell_stops:
        cur = float(o.price_open)
        if freeze_sell and not _inside_sup(cur):
            continue
        if abs(cur - sell_trig) >= 0.3:
            if mt5_client.modify_pending_order(o.ticket, price=sell_trig, reason="zone_sync"):
                add_log(f"📐 OUTSIDE ZONE: SellStop #{o.ticket} {cur} → {sell_trig}")


def _fill_stops(mt5_client, existing, trig, sl, tp_legs, order_type, lot, add_log):
    """Ensure 3 pending stops with magics from tp_legs [(magic, _), ...]."""
    by_magic = {}
    for o in existing:
        by_magic[int(getattr(o, "magic", 0) or 0)] = o
    placed = 0
    for i, (magic, tp) in enumerate(tp_legs):
        if magic in by_magic:
            continue
        if mt5_client.place_pending_order(
            config.SYMBOL, order_type, lot, trig, sl, tp, magic=magic
        ):
            placed += 1
            add_log(f"📌 SMC STOP: {order_type} @{trig} (outside zone) SL={sl} TP={tp} magic={magic} lot={lot}")
    return placed


def _widen_only_tp(mt5_client, pos, new_tp, add_log):
    """TP2/TP3 may only move farther from entry (more profit), never closer."""
    cur = float(pos.tp or 0)
    entry = float(pos.price_open)
    new_tp = float(new_tp)
    if pos.type == mt5.POSITION_TYPE_BUY:
        if cur > 0 and new_tp < cur:
            return False
        if new_tp <= entry:
            return False
    else:
        if cur > 0 and new_tp > cur:
            return False
        if new_tp >= entry:
            return False
    if abs(cur - new_tp) < 0.3:
        return False
    if mt5_client.modify_tp(pos.ticket, new_tp):
        add_log(f"📈 TP WIDEN #{pos.ticket}: {cur:.2f} → {new_tp:.2f}")
        return True
    return False


def _apply_tp1_be(mt5_client, positions, side, add_log, m1_df=None):
    """
    SL shifts to Break-Even and trails along M1 zones ONLY AFTER TP1 leg is hit/closed.
    When len(positions) < 3 (TP1 hit), the remaining TP2 & TP3 legs have SL moved to BE and trailed.
    """
    dig = _digits()
    if not positions:
        return
    
    # Only activate BE and Trailing AFTER TP1 leg is hit/closed (less than 3 positions active)
    if len(positions) >= 3:
        return

    sym_info = mt5.symbol_info(positions[0].symbol) or mt5.symbol_info(config.SYMBOL)
    stops_level = float(getattr(sym_info, "trade_stops_level", 0) or 0) * float(getattr(sym_info, "point", 0.01) or 0.01)
    min_dist = max(stops_level, 0.5)

    tick = mt5.symbol_info_tick(positions[0].symbol) or mt5.symbol_info_tick(config.SYMBOL)
    curr_price = float(tick.bid) if side == "BUY" else float(tick.ask)
    
    for p in positions:
        open_p = float(p.price_open)
        cur_sl = float(p.sl or 0)
        be_p = round(open_p, dig)
        
        # Desired BE level (at least entry price)
        target_sl = be_p
        
        # Dynamic M1 structure trailing along valid M1 zones (Swing Low/High, Support/Resistance OB) after TP1 hit
        if m1_df is not None and len(m1_df) > 5:
            last_h1 = m1_df.iloc[-1]
            if side == "BUY":
                cands = [
                    float(m1_df['low'].tail(5).min()),
                    float(last_h1.get('support', 0) or 0),
                    float(last_h1.get('last_low', 0) or 0),
                ]
                valid_cands = [c for c in cands if c > target_sl and c < curr_price - min_dist]
                if valid_cands:
                    target_sl = round(max(valid_cands), dig)
            else:
                cands = [
                    float(m1_df['high'].tail(5).max()),
                    float(last_h1.get('resistance', 0) or 0),
                    float(last_h1.get('last_high', 0) or 0),
                ]
                valid_cands = [c for c in cands if c > 0 and c < target_sl and c > curr_price + min_dist]
                if valid_cands:
                    target_sl = round(min(valid_cands), dig)

        # Cap target_sl so broker does not reject with Invalid Stops if price pulls back near entry
        if side == "BUY":
            max_allowed = round(curr_price - min_dist, dig)
            target_sl = min(target_sl, max_allowed)
        else:
            min_allowed = round(curr_price + min_dist, dig)
            target_sl = max(target_sl, min_allowed)
        
        # Apply SL modification if better than current SL
        should_update = False
        if side == "BUY" and (cur_sl < target_sl - 0.05 or cur_sl <= 0):
            should_update = True
        elif side == "SELL" and (cur_sl > target_sl + 0.05 or cur_sl <= 0):
            should_update = True
            
        if should_update:
            if mt5_client.modify_sl(p.ticket, target_sl):
                add_log(f"🔒 TP1 HIT -> TRAIL SL ({side}): #{p.ticket} SL → ${target_sl:.2f}")


def _publish_smc_ui(bot_state, st, m5_status=None, buy_n=0, sell_n=0, buy_stops_n=0, sell_stops_n=0,
                   buy_trig=None, sell_trig=None):
    """Live SMC panel payload for dashboard."""
    m5_status = m5_status or bot_state.get("m5_trend_status") or {}
    bias = m5_status.get("dual", st.get("bias") or "MIXED")
    phase = st.get("phase") or "FLAT"
    step = int(st.get("step") or 1)
    origin = st.get("origin")
    zp = st.get("zero_loss_price")
    phase_help = {
        "FLAT": "Waiting — both stops armed (outside zone)",
        "LONG_TREND": "Buys running — TP1→BE, widen TP2/TP3",
        "SHORT_TREND": "Sells running — TP1→BE, widen TP2/TP3",
        "HEDGE": "Step2 recovery — 2 cover @P + 1 runner zone TP",
        "RUNNER": "Step2 done — 1 runner kept; base stops re-armed",
        "NEUTRALIZE": "Step3+ flatten all at slight-profit P",
    }.get(phase, phase)
    bot_state["smc_recovery_ui"] = {
        "step": step,
        "phase": phase,
        "origin": origin,
        "bias": bias,
        "pmax": m5_status.get("pmax"),
        "halftrend": m5_status.get("halftrend"),
        "dual": bias,
        "zero_loss_price": zp,
        "runner_ticket": st.get("runner_ticket"),
        "cover_tickets": list(st.get("cover_tickets") or []),
        "hedge_opened": bool(st.get("hedge_opened")),
        "neutralize_opened": bool(st.get("neutralize_opened")),
        "buy_positions": int(buy_n),
        "sell_positions": int(sell_n),
        "buy_stops": int(buy_stops_n),
        "sell_stops": int(sell_stops_n),
        "buy_stop_price": buy_trig,
        "sell_stop_price": sell_trig,
        "phase_help": phase_help,
        "label": f"Step {step} · {phase}" + (f" · origin {origin}" if origin else ""),
    }


def manage_smc_pmax_recovery(mt5_client, bot_state, m1_df, m5_df, curr_ask, curr_bid, add_log, target_lot=None):
    st = ensure_state(bot_state)
    max_steps = int(getattr(config, "SMC_MAX_RECOVERY_STEPS", 3))
    dig = _digits()
    base_lot = float(target_lot if target_lot is not None else getattr(config, "PENDING_BASE_LOT", 0.02))
    tol = float(getattr(config, "SMC_BASKET_EQUAL_TOLERANCE", 1.0))
    neut_tol = float(getattr(config, "SMC_NEUTRALIZE_PROFIT_TOLERANCE", 2.0))

    m5_status = Strategy.m5_hit_confirm_status(m1_df, m5_df)
    bias = m5_status.get("dual", "MIXED")
    bot_state["m5_trend_status"] = m5_status

    buy_trig = sell_trig = None
    buy_pos = sell_pos = []
    buy_stops = sell_stops = []
    try:
        _manage_smc_body(
            mt5_client, bot_state, st, m1_df, m5_df, curr_ask, curr_bid, add_log,
            max_steps, dig, base_lot, tol, neut_tol, m5_status, bias,
        )
    finally:
        try:
            orders = list(mt5.orders_get() or [])
            positions = list(mt5_client.get_open_positions() or [])
            xau = [p for p in positions if "XAU" in str(getattr(p, "symbol", "")).upper()]
            buy_pos, sell_pos = _split_engine_sides(xau)
            buy_stops = _xau_stops(orders, True)
            sell_stops = _xau_stops(orders, False)
            lvls = bot_state.get("_smc_lvls") or {}
            buy_trig = lvls.get("buy_stop")
            sell_trig = lvls.get("sell_stop")
        except Exception:
            pass
        _publish_smc_ui(
            bot_state, st, m5_status,
            buy_n=len(buy_pos), sell_n=len(sell_pos),
            buy_stops_n=len(buy_stops), sell_stops_n=len(sell_stops),
            buy_trig=buy_trig, sell_trig=sell_trig,
        )


def _manage_smc_body(mt5_client, bot_state, st, m1_df, m5_df, curr_ask, curr_bid, add_log,
                     max_steps, dig, base_lot, tol, neut_tol, m5_status, bias):

    orders = list(mt5.orders_get() or [])
    positions = list(mt5_client.get_open_positions() or [])
    xau_pos = [p for p in positions if "XAU" in str(getattr(p, "symbol", "")).upper()]
    # Manual / foreign positions never drive recovery phase or stop cancel
    buy_pos, sell_pos = _split_engine_sides(xau_pos)
    buy_stops = _xau_stops(orders, True)
    sell_stops = _xau_stops(orders, False)

    sym_info = None
    for s in [config.SYMBOL + "c", "XAUUSDc", config.SYMBOL + "m", "XAUUSDm", config.SYMBOL]:
        sym_info = mt5.symbol_info(s)
        if sym_info:
            break
    broker_stops = float(getattr(sym_info, "trade_stops_level", 0) or 0) * float(getattr(sym_info, "point", 0.01) or 0.01)
    vmin, vmax, vstep = mt5_client.get_volume_constraints(config.SYMBOL)

    lvls = Strategy.get_validated_breakout_levels(m1_df, curr_ask, curr_bid, broker_stops)
    buy_c = h1_m5_pattern_gate(m1_df, m5_df, "BUY")
    sell_c = h1_m5_pattern_gate(m1_df, m5_df, "SELL")
    buy_confirmed = bool(bias == "BUY" and buy_c.get("ok"))
    sell_confirmed = bool(bias == "SELL" and sell_c.get("ok"))
    buy_trig = lvls["buy_stop"]
    sell_trig = lvls["sell_stop"]
    buy_trig, sell_trig = _ensure_stops_outside_zones(
        buy_trig, sell_trig, lvls, curr_ask, curr_bid, dig,
        confirmed_buy=buy_confirmed, confirmed_sell=sell_confirmed, broker_stops=broker_stops
    )
    lvls = dict(lvls)
    lvls["buy_stop"] = buy_trig
    lvls["sell_stop"] = sell_trig
    bot_state["_smc_lvls"] = lvls
    st["buy_zone"] = lvls["res_zone"]
    st["sell_zone"] = lvls["sup_zone"]
    m1_atr = float(lvls["m1_atr"])
    min_profit_close = max(0.5, float(getattr(config, "SMC_MIN_CLOSE_PROFIT_USD", 1.0)))

    # ── Approach gate on pending stops (Step 1 flat) ──
    if not buy_pos and not sell_pos:
        st["phase"] = "FLAT"
        st["step"] = 1
        st["origin"] = None
        st["prior_tickets"] = []
        st["reverse_tickets"] = []
        st["runner_ticket"] = None
        st["hedge_opened"] = False
        st["neutralize_opened"] = False
        st["topup_done"] = False
        st["be_exits_set"] = False
        st["cover_tickets"] = []
        st["zero_loss_price"] = None
        st["extra_buy_done"] = False
        st["extra_sell_done"] = False
        st["neutralize_opened"] = False

        # ── 3-Way Reversal Confluence Market Entry Check ──
        # When market turns around from near pending stop and PMax + HalfTrend + Candlestick Pattern ALL align:
        # Open 3 market trades in that direction and CANCEL ALL pending stops.
        sl0 = float(getattr(config, "SMC_PENDING_STOP_SL", 0.0))

        if buy_confirmed:
            _cancel_orders(buy_stops + sell_stops)
            _, b_tp1, b_tp2, b_tp3 = Strategy.calculate_pending_zone_sl_tp(
                m1_df, m5_df, "BUY", curr_ask, opposite_entry=curr_bid
            )
            r1 = mt5_client.open_order(config.SYMBOL, mt5.ORDER_TYPE_BUY, base_lot, sl0, b_tp1, magic=MAGIC_BREAKOUT_BUY)
            r2 = mt5_client.open_order(config.SYMBOL, mt5.ORDER_TYPE_BUY, base_lot, sl0, b_tp2, magic=MAGIC_BRK_TP2_BUY)
            r3 = mt5_client.open_order(config.SYMBOL, mt5.ORDER_TYPE_BUY, base_lot, sl0, b_tp3, magic=MAGIC_BRK_TP3_BUY)
            if r1 or r2 or r3:
                add_log(
                    f"⚡ REVERSAL MARKET ENTRY (BUY): PMax + HalfTrend + Candlestick pattern ALL confirmed BUY! "
                    f"Opened 3× market buys (lot={base_lot}) and cancelled all pending stop orders."
                )
                st.update({"phase": "LONG_TREND", "origin": "BUY", "step": 1, "prior_tickets": [], "original_leg_lot": base_lot})
                return

        if sell_confirmed:
            _cancel_orders(buy_stops + sell_stops)
            _, s_tp1, s_tp2, s_tp3 = Strategy.calculate_pending_zone_sl_tp(
                m1_df, m5_df, "SELL", curr_bid, opposite_entry=curr_ask
            )
            r1 = mt5_client.open_order(config.SYMBOL, mt5.ORDER_TYPE_SELL, base_lot, sl0, s_tp1, magic=MAGIC_BREAKOUT_SELL)
            r2 = mt5_client.open_order(config.SYMBOL, mt5.ORDER_TYPE_SELL, base_lot, sl0, s_tp2, magic=MAGIC_BRK_TP2_SELL)
            r3 = mt5_client.open_order(config.SYMBOL, mt5.ORDER_TYPE_SELL, base_lot, sl0, s_tp3, magic=MAGIC_BRK_TP3_SELL)
            if r1 or r2 or r3:
                add_log(
                    f"⚡ REVERSAL MARKET ENTRY (SELL): PMax + HalfTrend + Candlestick pattern ALL confirmed SELL! "
                    f"Opened 3× market sells (lot={base_lot}) and cancelled all pending stop orders."
                )
                st.update({"phase": "SHORT_TREND", "origin": "SELL", "step": 1, "prior_tickets": [], "original_leg_lot": base_lot})
                return

        # Both BuyStop + SellStop always armed (SL=0).
        # Near-hit: PMAX + HalfTrend + candle pattern ALL must match that side → KEEP; else MODIFY away.
        if buy_stops:
            gate = Strategy.should_confirm_or_modify_stop(
                "BUY_STOP", curr_ask, float(buy_stops[0].price_open), m1_df, m5_df, broker_stops
            )
            if gate["action"] == "MODIFY":
                for o in buy_stops:
                    mt5_client.modify_pending_order(o.ticket, price=round(gate["new_price"], dig), reason="near_hit_block")
                buy_trig = round(gate["new_price"], dig)
                buy_trig, sell_trig = _ensure_stops_outside_zones(
                    buy_trig, sell_trig, lvls, curr_ask, curr_bid, dig,
                    confirmed_buy=buy_confirmed, confirmed_sell=sell_confirmed, broker_stops=broker_stops
                )
                add_log(
                    f"🔧 HIT-CHECK: BuyStops → {buy_trig} "
                    f"(need C1 + C2 BUY + C3 closed candle = M5 bullish/continuation, M1 not bearish; now C1={gate.get('pmax')} C2={gate.get('halftrend')} C3={gate.get('candle')})"
                )
        if sell_stops:
            gate = Strategy.should_confirm_or_modify_stop(
                "SELL_STOP", curr_bid, float(sell_stops[0].price_open), m1_df, m5_df, broker_stops
            )
            if gate["action"] == "MODIFY":
                for o in sell_stops:
                    mt5_client.modify_pending_order(o.ticket, price=round(gate["new_price"], dig), reason="near_hit_block")
                sell_trig = round(gate["new_price"], dig)
                buy_trig, sell_trig = _ensure_stops_outside_zones(
                    buy_trig, sell_trig, lvls, curr_ask, curr_bid, dig,
                    confirmed_buy=buy_confirmed, confirmed_sell=sell_confirmed, broker_stops=broker_stops
                )
                add_log(
                    f"🔧 HIT-CHECK: SellStops → {sell_trig} "
                    f"(need C1 + C2 SELL + C3 closed candle = M5 bearish/continuation, M1 not bullish; now C1={gate.get('pmax')} C2={gate.get('halftrend')} C3={gate.get('candle')})"
                )

        # Final clamp + sync. Freeze side when PMAX+HalfTrend both agree on that side.
        buy_trig, sell_trig = _ensure_stops_outside_zones(
            buy_trig, sell_trig, lvls, curr_ask, curr_bid, dig,
            confirmed_buy=buy_confirmed, confirmed_sell=sell_confirmed, broker_stops=broker_stops
        )
        # Freeze parked price only when PMAX + HalfTrend + candle all agree on that side.
        _sync_pending_outside(
            mt5_client, buy_stops, sell_stops, buy_trig, sell_trig, dig, add_log,
            freeze_buy=buy_confirmed,
            freeze_sell=sell_confirmed,
            lvls=lvls,
        )
        orders = list(mt5.orders_get() or [])
        buy_stops = _xau_stops(orders, True)
        sell_stops = _xau_stops(orders, False)

        _, b_tp1, b_tp2, b_tp3 = Strategy.calculate_pending_zone_sl_tp(
            m1_df, m5_df, "BUY", buy_trig, opposite_entry=sell_trig
        )
        _, s_tp1, s_tp2, s_tp3 = Strategy.calculate_pending_zone_sl_tp(
            m1_df, m5_df, "SELL", sell_trig, opposite_entry=buy_trig
        )
        buy_legs = [
            (MAGIC_BREAKOUT_BUY, b_tp1),
            (MAGIC_BRK_TP2_BUY, b_tp2),
            (MAGIC_BRK_TP3_BUY, b_tp3),
        ]
        sell_legs = [
            (MAGIC_BREAKOUT_SELL, s_tp1),
            (MAGIC_BRK_TP2_SELL, s_tp2),
            (MAGIC_BRK_TP3_SELL, s_tp3),
        ]
        _fill_stops(mt5_client, buy_stops, buy_trig, sl0, buy_legs, "BUY_STOP", base_lot, add_log)
        _fill_stops(mt5_client, sell_stops, sell_trig, sl0, sell_legs, "SELL_STOP", base_lot, add_log)
        st["original_leg_lot"] = base_lot
        return

    # ── Runner-only after Step2B: keep 1 reverse @ BE+zone TP; re-arm BOTH base stops ──
    if (
        ((not buy_pos and len(sell_pos) == 1) or (not sell_pos and len(buy_pos) == 1))
        and (
            st.get("phase") in ("HEDGE", "RUNNER")
            or st.get("runner_ticket")
            or st.get("cover_tickets")
        )
    ):
        runner = sell_pos[0] if sell_pos else buy_pos[0]
        side = "SELL" if sell_pos else "BUY"
        # Promote from hedge if broker already closed cover+priors
        if st.get("phase") == "HEDGE" or st.get("cover_tickets"):
            add_log(f"✅ STEP2B DONE (broker): cover+priors flat → runner #{runner.ticket} kept")
        st["phase"] = "RUNNER"
        st["runner_ticket"] = runner.ticket
        st["hedge_opened"] = False
        st["be_exits_set"] = False
        st["cover_tickets"] = []
        be = round(float(runner.price_open), dig)
        tick = mt5.symbol_info_tick(runner.symbol)
        bid = float(getattr(tick, "bid", 0) or 0)
        ask = float(getattr(tick, "ask", 0) or 0)
        if side == "SELL" and bid > 0 and be > bid + 0.2:
            mt5_client.modify_sl(runner.ticket, be)
        elif side == "BUY" and ask > 0 and be < ask - 0.2:
            mt5_client.modify_sl(runner.ticket, be)
        _, tp1, _, _ = Strategy.calculate_pending_zone_sl_tp(m1_df, m5_df, side, float(runner.price_open))
        mt5_client.modify_tp(runner.ticket, round(float(tp1), dig))
        _place_both_base_stops(
            mt5_client, m1_df, m5_df, buy_trig, sell_trig, st, lvls, base_lot, add_log, buy_stops, sell_stops, bias=bias
        )
        return

    # ── Trend active ──
    if buy_pos and not sell_pos:
        # Cancel leftover buy stops + ALL sell stops (no reverse-at-SL)
        if buy_stops:
            _cancel_orders(buy_stops)
            add_log("🧹 SMC: leftover BuyStops cancelled (buys running)")
        if sell_stops:
            _cancel_orders(sell_stops)
            add_log("🧹 SMC: all SellStops removed while BUY trend runs (no SL glue)")
        if st.get("phase") not in ("LONG_TREND", "HEDGE", "NEUTRALIZE", "RUNNER"):
            st["phase"] = "LONG_TREND"
        st["origin"] = "BUY"
        st["prior_tickets"] = [p.ticket for p in buy_pos]
        st["original_leg_lot"] = float(buy_pos[0].volume)
        _apply_tp1_be(mt5_client, buy_pos, "BUY", add_log, m1_df=m1_df)
        # Widen TP2/TP3 toward farther M1 targets only
        _, _, tp2, tp3 = Strategy.calculate_pending_zone_sl_tp(m1_df, m5_df, "BUY", float(buy_pos[0].price_open))
        for p in buy_pos:
            mag = int(getattr(p, "magic", 0) or 0)
            if mag in (MAGIC_BRK_TP2_BUY, MAGIC_TP2_BUY):
                _widen_only_tp(mt5_client, p, tp2, add_log)
            elif mag in (MAGIC_BRK_TP3_BUY, MAGIC_TP3_BUY):
                _widen_only_tp(mt5_client, p, tp3, add_log)

        # Dual reverse signal
        if bias == "SELL" and st.get("phase") == "LONG_TREND" and not st.get("hedge_opened"):
            pnl = _basket_pnl(buy_pos, "BUY")
            if pnl >= min_profit_close:
                for p in list(buy_pos):
                    mt5_client.close_position(p.ticket)
                add_log(f"✅ PROFIT EXIT: buys ${pnl:.2f} closed → re-arm base BuyStop+SellStop")
                st.update({"step": 1, "phase": "FLAT", "origin": None, "hedge_opened": False,
                           "be_exits_set": False, "cover_tickets": [], "runner_ticket": None,
                           "last_recovery_signal": None})
                return
            existing_rec = [p for p in xau_pos if int(getattr(p, "magic", 0) or 0) in set(REC_SELL_MAGICS) | {100909}]
            if existing_rec:
                st.update({
                    "step": 2, "phase": "HEDGE", "hedge_opened": True, "origin": "BUY",
                    "last_recovery_signal": "SELL", "neutralize_opened": False,
                })
                add_log(f"🧯 STEP2 already has {len(existing_rec)} SELL recovery leg(s) — skip re-open")
                return
            # LOSS → Step2: open 2 sells — 1 cover recovers ALL same-side market loss (incl manual) in ~50 pips; 1 runner
            move = _recovery_target_move(m1_atr)
            P = round(float(curr_bid) - move, dig)
            mpl1 = _mpl1(mt5_client)
            buf = float(getattr(config, "SMC_ZERO_LOSS_BUFFER_USD", 5.0))
            mult = float(getattr(config, "SMC_RECOVERY_PROFIT_MULTIPLIER", 1.5))
            market_buys = _xau_side_positions(xau_pos, "BUY", exclude_magics=REC_REVERSE_MAGICS)
            proj = (_projected_loss_at_price(market_buys, "BUY", P, mpl1) * mult) + buf
            mpl_m = mt5_client.money_per_lot_for_move(config.SYMBOL, move)
            cover_lot = _calc_recovery_leg_lot(proj, move, mpl_m, 1, vmin, vstep, vmax)
            runner_lot = min(max(float(base_lot), vmin), vmax)
            add_log(
                f"⚡ STEP2 SELL-HEDGE: market_buys={len(market_buys)} proj_target@P=${proj:.2f} (100% loss + 50% profit) P={P} "
                f"→ cover={cover_lot} runner={runner_lot}"
            )
            _remove_sl(mt5_client, market_buys, add_log)
            _open_step2_hedge_pair(mt5_client, "SELL", cover_lot, runner_lot, add_log)
            positions = list(mt5_client.get_open_positions() or [])
            xau_now = [p for p in positions if "XAU" in str(getattr(p, "symbol", "")).upper()]
            priors = _xau_side_positions(xau_now, "BUY", exclude_magics=REC_REVERSE_MAGICS)
            _, sells = _split_engine_sides(xau_now)
            zp, cover = _apply_step2_hedge_exits(mt5_client, priors, sells, P, m1_df, m5_df, add_log)
            st.update({
                "step": 2, "phase": "HEDGE", "hedge_opened": True, "origin": "BUY",
                "be_exits_set": bool(zp), "zero_loss_price": zp or P,
                "cover_tickets": [c.ticket for c in cover],
                "runner_ticket": next((p.ticket for p in sells if p.ticket not in {c.ticket for c in cover}), None),
                "topup_done": False,
                "last_recovery_signal": "SELL",
            })
        return

    if sell_pos and not buy_pos:
        if sell_stops:
            _cancel_orders(sell_stops)
            add_log("🧹 SMC: leftover SellStops cancelled (sells running)")
        if buy_stops:
            _cancel_orders(buy_stops)
            add_log("🧹 SMC: all BuyStops removed while SELL trend runs")
        if st.get("phase") not in ("SHORT_TREND", "HEDGE", "NEUTRALIZE", "RUNNER"):
            st["phase"] = "SHORT_TREND"
        st["origin"] = "SELL"
        st["prior_tickets"] = [p.ticket for p in sell_pos]
        st["original_leg_lot"] = float(sell_pos[0].volume)
        _apply_tp1_be(mt5_client, sell_pos, "SELL", add_log, m1_df=m1_df)
        _, _, tp2, tp3 = Strategy.calculate_pending_zone_sl_tp(m1_df, m5_df, "SELL", float(sell_pos[0].price_open))
        for p in sell_pos:
            mag = int(getattr(p, "magic", 0) or 0)
            if mag in (MAGIC_BRK_TP2_SELL, MAGIC_TP2_SELL):
                _widen_only_tp(mt5_client, p, tp2, add_log)
            elif mag in (MAGIC_BRK_TP3_SELL, MAGIC_TP3_SELL):
                _widen_only_tp(mt5_client, p, tp3, add_log)

        if bias == "BUY" and st.get("phase") == "SHORT_TREND" and not st.get("hedge_opened"):
            pnl = _basket_pnl(sell_pos, "SELL")
            if pnl >= min_profit_close:
                for p in list(sell_pos):
                    mt5_client.close_position(p.ticket)
                add_log(f"✅ PROFIT EXIT: sells ${pnl:.2f} closed → re-arm base stops")
                st.update({"step": 1, "phase": "FLAT", "origin": None, "hedge_opened": False,
                           "be_exits_set": False, "cover_tickets": [], "runner_ticket": None,
                           "last_recovery_signal": None})
                return
            # Already have recovery buys? stay Step2 — do not open 3rd/duplicate legs
            existing_rec = [p for p in xau_pos if int(getattr(p, "magic", 0) or 0) in set(REC_BUY_MAGICS) | {100907}]
            if existing_rec:
                st.update({
                    "step": 2, "phase": "HEDGE", "hedge_opened": True, "origin": "SELL",
                    "last_recovery_signal": "BUY", "neutralize_opened": False,
                })
                add_log(f"🧯 STEP2 already has {len(existing_rec)} BUY recovery leg(s) — skip re-open")
                return
            move = _recovery_target_move(m1_atr)
            P = round(float(curr_ask) + move, dig)
            mpl1 = _mpl1(mt5_client)
            buf = float(getattr(config, "SMC_ZERO_LOSS_BUFFER_USD", 5.0))
            mult = float(getattr(config, "SMC_RECOVERY_PROFIT_MULTIPLIER", 1.5))
            market_sells = _xau_side_positions(xau_pos, "SELL", exclude_magics=REC_REVERSE_MAGICS)
            proj = (_projected_loss_at_price(market_sells, "SELL", P, mpl1) * mult) + buf
            mpl_m = mt5_client.money_per_lot_for_move(config.SYMBOL, move)
            cover_lot = _calc_recovery_leg_lot(proj, move, mpl_m, 1, vmin, vstep, vmax)
            runner_lot = min(max(float(base_lot), vmin), vmax)
            add_log(
                f"⚡ STEP2 BUY-HEDGE: market_sells={len(market_sells)} proj_target@P=${proj:.2f} (100% loss + 50% profit) P={P} "
                f"→ cover={cover_lot} runner={runner_lot}"
            )
            _remove_sl(mt5_client, market_sells, add_log)
            _open_step2_hedge_pair(mt5_client, "BUY", cover_lot, runner_lot, add_log)
            positions = list(mt5_client.get_open_positions() or [])
            xau_now = [p for p in positions if "XAU" in str(getattr(p, "symbol", "")).upper()]
            priors = _xau_side_positions(xau_now, "SELL", exclude_magics=REC_REVERSE_MAGICS)
            buys, _ = _split_engine_sides(xau_now)
            zp, cover = _apply_step2_hedge_exits(mt5_client, priors, buys, P, m1_df, m5_df, add_log)
            st.update({
                "step": 2, "phase": "HEDGE", "hedge_opened": True, "origin": "SELL",
                "be_exits_set": bool(zp), "zero_loss_price": zp or P,
                "cover_tickets": [c.ticket for c in cover],
                "runner_ticket": next((p.ticket for p in buys if p.ticket not in {c.ticket for c in cover}), None),
                "topup_done": False,
                "last_recovery_signal": "BUY",
            })
        return

    # ── Hedge both sides (Step 2 keep runner / Step 3+ flatten all) ──
    if buy_pos and sell_pos:
        origin = st.get("origin") or ("BUY" if len(buy_pos) >= len(sell_pos) else "SELL")
        if buy_stops or sell_stops:
            _cancel_orders(buy_stops + sell_stops)

        # Market-wide same-side baskets (engine + manual) for Step2 recovery accounting
        market_buys = _xau_side_positions(xau_pos, "BUY", exclude_magics=REC_REVERSE_MAGICS)
        market_sells = _xau_side_positions(xau_pos, "SELL", exclude_magics=REC_REVERSE_MAGICS)
        market_buy_pnl = sum(_pos_pnl(p) for p in market_buys)
        market_sell_pnl = sum(_pos_pnl(p) for p in market_sells)

        sell_pnl = _basket_pnl(sell_pos, "SELL")
        buy_pnl = _basket_pnl(buy_pos, "BUY")
        move = _recovery_target_move(m1_atr)
        mpl1 = _mpl1(mt5_client)
        buf = float(getattr(config, "SMC_ZERO_LOSS_BUFFER_USD", 5.0))
        mult = float(getattr(config, "SMC_RECOVERY_PROFIT_MULTIPLIER", 1.5))
        step_n = int(st.get("step", 2) or 2)

        # Repair / lock Step2 state from live tickets (prevents false Step3 jump after restart)
        rec_buys = [p for p in buy_pos if int(getattr(p, "magic", 0) or 0) in REC_REVERSE_MAGICS]
        rec_sells = [p for p in sell_pos if int(getattr(p, "magic", 0) or 0) in REC_REVERSE_MAGICS]
        has_extra_sell = any(int(getattr(p, "magic", 0) or 0) == MAGIC_EXTRA_SELL for p in sell_pos)
        has_extra_buy = any(int(getattr(p, "magic", 0) or 0) == MAGIC_EXTRA_BUY for p in buy_pos)
        # Origin SELL + REC buys = Step2 until a true opposite EXTRA SELL appears
        if rec_buys and market_sells and not has_extra_sell:
            if st.get("neutralize_opened") or int(st.get("step", 2) or 2) >= 3:
                add_log("🧯 STEP repair: false Step3→back to Step2 HEDGE (origin SELL, waiting opposite SELL signal)")
            st["origin"] = "SELL"
            st["step"] = 2
            st["phase"] = "HEDGE"
            st["hedge_opened"] = True
            st["neutralize_opened"] = False
            st["last_recovery_signal"] = "BUY"
            origin = "SELL"
            step_n = 2
        elif rec_sells and market_buys and not has_extra_buy:
            if st.get("neutralize_opened") or int(st.get("step", 2) or 2) >= 3:
                add_log("🧯 STEP repair: false Step3→back to Step2 HEDGE (origin BUY, waiting opposite BUY signal)")
            st["origin"] = "BUY"
            st["step"] = 2
            st["phase"] = "HEDGE"
            st["hedge_opened"] = True
            st["neutralize_opened"] = False
            st["last_recovery_signal"] = "SELL"
            origin = "BUY"
            step_n = 2

        # ── STEP2 ONLY: while step<=2 and not neutralize, never open Step3 extras ──
        if step_n <= 2 and not st.get("neutralize_opened"):
            if origin == "BUY" and market_buy_pnl < 0:
                P = float(st.get("zero_loss_price") or 0) or round(float(curr_bid) - move, dig)
                zp, cover = _apply_step2_hedge_exits(mt5_client, market_buys, sell_pos, P, m1_df, m5_df, add_log)
                st["zero_loss_price"] = zp or P
                st["be_exits_set"] = bool(zp)
                st["cover_tickets"] = [c.ticket for c in cover]
                cover_ids = set(st["cover_tickets"])
                cover_pos = [p for p in sell_pos if p.ticket in cover_ids]
                cover_pnl = sum(_pos_pnl(p) for p in cover_pos)
                target_pnl = (abs(market_buy_pnl) * mult) + buf + min_profit_close
                if cover_pos and cover_pnl >= target_pnl:
                    runners = [p for p in sell_pos if p.ticket not in cover_ids]
                    for p in list(market_buys) + cover_pos:
                        mt5_client.close_position(p.ticket)
                    add_log(
                        f"✅ STEP2 DONE: all buys+1 cover closed with 100% loss recovery + 50% profit buffer "
                        f"(target=${target_pnl:.2f}); runner kept={len(runners)}"
                    )
                    if runners:
                        r0 = runners[0]
                        be = round(float(r0.price_open), dig)
                        if mt5.symbol_info_tick(r0.symbol) and be > float(mt5.symbol_info_tick(r0.symbol).bid) + 0.2:
                            mt5_client.modify_sl(r0.ticket, be)
                        _, tp1, _, _ = Strategy.calculate_pending_zone_sl_tp(m1_df, m5_df, "SELL", float(r0.price_open))
                        mt5_client.modify_tp(r0.ticket, round(float(tp1), dig))
                        st["runner_ticket"] = r0.ticket
                    st.update({"phase": "RUNNER", "hedge_opened": False, "be_exits_set": False, "cover_tickets": []})
                    orders = list(mt5.orders_get() or [])
                    _place_both_base_stops(
                        mt5_client, m1_df, m5_df, buy_trig, sell_trig, st, lvls, base_lot, add_log,
                        _xau_stops(orders, True), _xau_stops(orders, False), bias=bias,
                    )
                return

            if origin == "SELL" and market_sell_pnl < 0:
                P = float(st.get("zero_loss_price") or 0) or round(float(curr_ask) + move, dig)
                zp, cover = _apply_step2_hedge_exits(mt5_client, market_sells, buy_pos, P, m1_df, m5_df, add_log)
                st["zero_loss_price"] = zp or P
                st["be_exits_set"] = bool(zp)
                st["cover_tickets"] = [c.ticket for c in cover]
                cover_ids = set(st["cover_tickets"])
                cover_pos = [p for p in buy_pos if p.ticket in cover_ids]
                cover_pnl = sum(_pos_pnl(p) for p in cover_pos)
                target_pnl = (abs(market_sell_pnl) * mult) + buf + min_profit_close
                if cover_pos and cover_pnl >= target_pnl:
                    runners = [p for p in buy_pos if p.ticket not in cover_ids]
                    for p in list(market_sells) + cover_pos:
                        mt5_client.close_position(p.ticket)
                    add_log(
                        f"✅ STEP2 DONE: all sells+1 cover closed with 100% loss recovery + 50% profit buffer "
                        f"(target=${target_pnl:.2f}); runner kept={len(runners)}"
                    )
                    if runners:
                        r0 = runners[0]
                        be = round(float(r0.price_open), dig)
                        tick = mt5.symbol_info_tick(r0.symbol)
                        if tick and be < float(tick.ask) - 0.2:
                            mt5_client.modify_sl(r0.ticket, be)
                        _, tp1, _, _ = Strategy.calculate_pending_zone_sl_tp(m1_df, m5_df, "BUY", float(r0.price_open))
                        mt5_client.modify_tp(r0.ticket, round(float(tp1), dig))
                        st["runner_ticket"] = r0.ticket
                    st.update({"phase": "RUNNER", "hedge_opened": False, "be_exits_set": False, "cover_tickets": []})
                    orders = list(mt5.orders_get() or [])
                    _place_both_base_stops(
                        mt5_client, m1_df, m5_df, buy_trig, sell_trig, st, lvls, base_lot, add_log,
                        _xau_stops(orders, True), _xau_stops(orders, False), bias=bias,
                    )
                return

            # Step2 active but no close yet — stay on Step2 (do not neutralize)
            return

        # Step 3+ strict alternating reverse ONLY:
        # last BUY recovery → next must be SELL (bias SELL + buys losing)
        # last SELL recovery → next must be BUY (bias BUY + sells losing)
        last_sig = st.get("last_recovery_signal")
        reverse_signal = None
        if last_sig == "BUY" and bias == "SELL" and buy_pnl < 0:
            reverse_signal = "SELL"
        elif last_sig == "SELL" and bias == "BUY" and sell_pnl < 0:
            reverse_signal = "BUY"

        if reverse_signal and step_n < 10:
            st["step"] = max(3, step_n + 1)
            st["phase"] = "NEUTRALIZE"
            st["neutralize_opened"] = True
            st["last_recovery_signal"] = reverse_signal

            if reverse_signal == "SELL":
                P = round(float(curr_bid) - move, dig)
                buy_loss = (_projected_loss_at_price(buy_pos, "BUY", P, mpl1) * mult) + buf
                sell_gain = sum(max(0.0, (float(p.price_open) - P) * float(p.volume) * mpl1) for p in sell_pos)
                need = max(0.0, buy_loss - sell_gain)
                mpl_m = mt5_client.money_per_lot_for_move(config.SYMBOL, move)
                leg_lot = _calc_recovery_leg_lot(need, move, mpl_m, 1, vmin, vstep, vmax)
                if leg_lot >= vmin:
                    add_log(
                        f"🔄 STEP{st['step']} NEW SELL reverse: buy_loss_target@{P}=${buy_loss:.2f} "
                        f"need=${need:.2f} -> open 1x SELL lot={leg_lot}"
                    )
                    _open_one(mt5_client, "SELL", leg_lot, MAGIC_EXTRA_SELL, add_log)
            else:
                P = round(float(curr_ask) + move, dig)
                sell_loss = (_projected_loss_at_price(sell_pos, "SELL", P, mpl1) * mult) + buf
                buy_gain = sum(max(0.0, (P - float(p.price_open)) * float(p.volume) * mpl1) for p in buy_pos)
                need = max(0.0, sell_loss - buy_gain)
                mpl_m = mt5_client.money_per_lot_for_move(config.SYMBOL, move)
                leg_lot = _calc_recovery_leg_lot(need, move, mpl_m, 1, vmin, vstep, vmax)
                if leg_lot >= vmin:
                    add_log(
                        f"🔄 STEP{st['step']} NEW BUY reverse: sell_loss_target@{P}=${sell_loss:.2f} "
                        f"need=${need:.2f} -> open 1x BUY lot={leg_lot}"
                    )
                    _open_one(mt5_client, "BUY", leg_lot, MAGIC_EXTRA_BUY, add_log)

            positions = list(mt5_client.get_open_positions() or [])
            buy_pos, sell_pos = _split_engine_sides(positions)
            if reverse_signal == "SELL":
                zp = _apply_full_flatten_exits(mt5_client, buy_pos, sell_pos, add_log)
            else:
                zp = _apply_full_flatten_exits(mt5_client, sell_pos, buy_pos, add_log)
            st["zero_loss_price"] = zp
            st["be_exits_set"] = bool(zp)
            return

        # Step3 to 10 recovery handling (strict):
        # no step increase unless a NEW opposite reverse signal event appears (handled above).
        if st.get("phase") == "NEUTRALIZE" or step_n >= 3:
            # Emergency Circuit Breaker: Max 10 steps. At Step 11, close all positions immediately to protect capital.
            if step_n >= 11:
                for p in buy_pos + sell_pos:
                    mt5_client.close_position(p.ticket)
                add_log(f"⚠️ EMERGENCY EXIT: Step 11 circuit breaker reached! All trades closed to prevent further loss.")
                st.update({
                    "step": 1, "phase": "FLAT", "origin": None, "hedge_opened": False,
                    "topup_done": False, "be_exits_set": False, "cover_tickets": [],
                    "neutralize_opened": False, "runner_ticket": None, "last_recovery_signal": None,
                })
                orders = list(mt5.orders_get() or [])
                _place_both_base_stops(
                    mt5_client, m1_df, m5_df, buy_trig, sell_trig, st, lvls, base_lot, add_log,
                    _xau_stops(orders, True), _xau_stops(orders, False), bias=bias,
                )
                return

            if not st.get("be_exits_set"):
                if sell_pnl <= buy_pnl:
                    zp = _apply_full_flatten_exits(mt5_client, sell_pos, buy_pos, add_log)
                else:
                    zp = _apply_full_flatten_exits(mt5_client, buy_pos, sell_pos, add_log)
                st["zero_loss_price"] = zp
                st["be_exits_set"] = bool(zp)
            
            net = sum(_pos_pnl(p) for p in buy_pos + sell_pos)
            if net >= min_profit_close:
                for p in buy_pos + sell_pos:
                    mt5_client.close_position(p.ticket)
                add_log(f"🏁 STEP{step_n} FLAT net=${net:.2f} → ALL closed; re-arm base stops")
                st.update({
                    "step": 1, "phase": "FLAT", "origin": None, "hedge_opened": False,
                    "topup_done": False, "be_exits_set": False, "cover_tickets": [],
                    "neutralize_opened": False, "runner_ticket": None, "last_recovery_signal": None,
                })
        return
