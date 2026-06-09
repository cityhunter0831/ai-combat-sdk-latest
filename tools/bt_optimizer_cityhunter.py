"""
BT Optimizer — cityhunter 전용

cityhunter.yaml의 v4.0 레이어 구조를 유지하면서
각 레이어의 수치 파라미터를 LHS + 정제(refine) 방식으로 탐색한다.

v4.0 구조 (v3.2-opt 대비 변경):
  L3: 수세 BFM (구 L7, 방어 우선으로 이동, 감지 거리 확대)
  L4: LagPreempt (구 L5, 방어 뒤로 이동)
  L5: 공세 BFM (HighYoYo, TwoCircleFight, OneCircleFight, BarrelRoll, LagPursuit 추가)
  L6: 접근/중립 (LowYoYo 추가)

Scoring (bt_optimizer.py와 동일 hierarchical 원칙):
  WIN_BASE=10, DRAW_BASE=1, LOSS_BASE=-5, HP_WEIGHT=2
  → worst_win(8.0) > best_draw(3.0) > best_loss(-3.0)

Usage:
    python tools/bt_optimizer_cityhunter.py --candidates 50 --workers 4
    python tools/bt_optimizer_cityhunter.py --validate --rounds 10
"""

import sys
import os
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
import yaml
import json
import time
import argparse
import multiprocessing as mp
from pathlib import Path
from copy import deepcopy
from datetime import datetime

import numpy as np

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# Scoring Constants (bt_optimizer.py와 동일)
# ============================================================

WIN_BASE  = 10.0
DRAW_BASE =  1.0
LOSS_BASE = -5.0
HP_WEIGHT =  2.0

# ============================================================
# Opponents
# ============================================================

OPPONENTS = ["simple", "aggressive", "golden", "ace", "eagle1"]

# ============================================================
# Parameter Space — cityhunter 9-layer 구조 기반
# ============================================================

PARAM_SPACE = {
    # ── L1: HardDeck ──
    "hard_deck_ft":        {"type": "continuous", "range": (500,  1500)},
    "climb_target_ft":     {"type": "continuous", "range": (1500, 5000)},

    # ── L2: InEnemyWEZ → BreakTurn ──
    "wez_dist_ft":         {"type": "continuous", "range": (300,   700)},  # 상한 700 — BreakTurn 과발동 방지
    "wez_los_deg":         {"type": "continuous", "range": (2.0,  15.0)},

    # ── L5: ClosureRateAbove + DistanceBelow → LagPursuit ──
    "lag_closure_kts":     {"type": "continuous", "range": (100,  400)},
    "lag_dist_ft":         {"type": "continuous", "range": (1000, 4000)},

    # ── L6 진입: ATABelow + DistanceBelow ──
    "off_ata_deg":         {"type": "continuous", "range": (20.0, 70.0)},
    "off_dist_ft":         {"type": "continuous", "range": (5000, 15000)},

    # ── L6-1: WEZ 진입 → PNAttack ──
    "gun_dist_ft":         {"type": "continuous", "range": (600,  3000)},
    "gun_ata_deg":         {"type": "continuous", "range": (4.0,  15.0)},

    # ── L6-1b: 써클파이트 거리 (IsTwoCircle/IsOneCircle) ──
    "circle_fight_dist_ft":{"type": "continuous", "range": (3000, 7000)},  # 하한 3000 — WEZ보다 넓게 보장

    # ── L6-3: 중거리 공세 → LeadPursuit ──
    # (off_dist_ft 재사용)

    # ── L7 진입: UnderThreat + DistanceBelow ──
    "def_threat_aa_deg":   {"type": "continuous", "range": (80.0, 130.0)},
    "def_dist_ft":         {"type": "continuous", "range": (2500, 6000)},  # 하한 2500 — eagle1 교전거리(P50=4089ft) 포함

    # ── L7 내부 ──
    "def_break_aa_deg":    {"type": "continuous", "range": (110.0, 160.0)},
    "def_maneuver_aa_deg": {"type": "continuous", "range": (60.0,  125.0)},

    # ── L7-4: HighYoYo (속도 과잉 + IsMerged) ──
    "vel_high_kts":        {"type": "continuous", "range": (450.0, 550.0)},  # 상한 550 — HighYoYo 실제 발동
    "highy_merge_dist_ft": {"type": "continuous", "range": (1000, 3000)},

    # ── L8: PNPursuit 근거리 분기 ──
    "pn_close_dist_ft":    {"type": "continuous", "range": (3000, 6000)},  # 하한 3000 — L8-1 PNPursuit 보장
}


# ============================================================
# BT YAML 생성 — cityhunter 9-layer 구조
# ============================================================

def generate_bt_yaml(params):
    """
    파라미터 딕셔너리 → cityhunter v4.1 BT YAML 딕셔너리.

    Layer 순서 (priority high → low):
      L1: BelowHardDeck → ClimbTo
      L2: InEnemyWEZ → BreakTurn
      L5: ClosureRateAbove + DistanceBelow → LagPursuit
      L6: ATABelow + DistanceBelow → [PNAttack | CircleFight(dist<X) | LeadPursuit]
      L7: UnderThreat + DistanceBelow → [BreakTurn | DefensiveManeuver | CounterattackOnOvershoot | HighYoYo]
      L8: [PNPursuit(close) | PNPursuit(fallback)]
      L9: Pursue (fallback)
    """
    C = lambda name, **kw: {"type": "Condition", "name": name, **({"params": kw} if kw else {})}
    A = lambda name, **kw: {"type": "Action",    "name": name, **({"params": kw} if kw else {})}

    def Seq(name, children):
        return {"type": "Sequence", "name": name, "children": children}

    def Sel(name, children):
        return {"type": "Selector", "name": name, "children": children}

    children = []

    # ── L1: HardDeck ──
    children.append(Seq("L1_hard_deck", [
        C("BelowHardDeck", threshold_ft=int(params["hard_deck_ft"])),
        A("ClimbTo", target_altitude_ft=int(params["climb_target_ft"])),
    ]))

    # ── L2: InEnemyWEZ → BreakTurn ──
    children.append(Seq("L2_immediate_threat", [
        C("InEnemyWEZ",
          max_distance_ft=round(float(params["wez_dist_ft"]), 0),
          max_los_angle_deg=round(float(params["wez_los_deg"]), 1)),
        A("BreakTurn"),
    ]))

    # ── L5: LagPreempt ──
    children.append(Seq("L5_lag_preempt", [
        C("ClosureRateAbove", threshold_kts=round(float(params["lag_closure_kts"]), 0)),
        C("DistanceBelow",   threshold_ft=int(params["lag_dist_ft"])),
        A("LagPursuit"),
    ]))

    # ── L6: OffensiveBFM ──
    children.append(Seq("L6_offensive_bfm", [
        C("ATABelow",    threshold_deg=round(float(params["off_ata_deg"]), 1)),
        C("DistanceBelow", threshold_ft=int(params["off_dist_ft"])),
        Sel("L6_offensive_phases", [
            # L6-1: WEZ 진입 → PNAttack
            Seq("L6_1_wez_attack", [
                C("DistanceBelow", threshold_ft=int(params["gun_dist_ft"])),
                C("ATABelow",      threshold_deg=round(float(params["gun_ata_deg"]), 1)),
                A("PNAttack"),
            ]),
            # L6-1b: 근거리 써클파이트 (IsTwoCircle/IsOneCircle 자동 판단, BEM §4.4.11)
            Seq("L6_1b_circle_fight", [
                C("DistanceBelow", threshold_ft=int(params["circle_fight_dist_ft"])),
                Sel("L6_1b_circle_sel", [
                    Seq("L6_1b_two", [C("IsTwoCircle"), A("TwoCircleFight")]),
                    Seq("L6_1b_one", [C("IsOneCircle"), A("OneCircleFight")]),
                    A("TCFight"),
                ]),
            ]),
            # L6-3: 중거리 공세 → LeadPursuit
            Seq("L6_3_lead_pursuit", [
                C("DistanceBelow", threshold_ft=int(params["off_dist_ft"])),
                A("LeadPursuit"),
            ]),
        ]),
    ]))

    # ── L7: DefensiveBFM (HighYoYo 조건부 추가) ──
    children.append(Seq("L7_defensive_bfm", [
        C("UnderThreat",   aa_threshold_deg=round(float(params["def_threat_aa_deg"]), 0)),
        C("DistanceBelow", threshold_ft=int(params["def_dist_ft"])),
        Sel("L7_defensive_phases", [
            # L7-1: 심각한 위협 → BreakTurn
            Seq("L7_1_break_turn", [
                C("UnderThreat", aa_threshold_deg=round(float(params["def_break_aa_deg"]), 0)),
                A("BreakTurn"),
            ]),
            # L7-2: 일반 위협 → DefensiveManeuver
            Seq("L7_2_def_maneuver", [
                C("UnderThreat", aa_threshold_deg=round(float(params["def_maneuver_aa_deg"]), 0)),
                A("DefensiveManeuver"),
            ]),
            # L7-3: 오버슈트 감지 → 역공격
            Seq("L7_3_counterattack", [
                C("IsOvershootRisk"),
                A("CounterattackOnOvershoot"),
            ]),
            # L7-4: 속도 과잉 + 근접 → HighYoYo (BEM §4.4)
            Seq("L7_4_high_yoyo", [
                C("VelocityAbove", min_velocity_kts=round(float(params["vel_high_kts"]), 0)),
                C("IsMerged", merge_threshold_ft=int(params["highy_merge_dist_ft"])),
                A("HighYoYo"),
            ]),
        ]),
    ]))

    # ── L8: PNPursuit (catch-all) ──
    children.append(Seq("L8_approach_bfm", [
        Sel("L8_approach_phases", [
            Seq("L8_1_close_fight", [
                C("DistanceBelow", threshold_ft=int(params["pn_close_dist_ft"])),
                A("PNPursuit"),
            ]),
            Seq("L8_2_pn_pursuit", [
                A("PNPursuit"),
            ]),
        ]),
    ]))

    # ── L9: Pursue fallback ──
    children.append(A("Pursue"))

    return {
        "name": "cityhunter",
        "version": "4.1-opt",
        "description": "v4.1 optimizer-generated (v3.2-opt 복원 + 교범 기동 조건부)",
        "tree": {"type": "Selector", "name": "root", "children": children},
    }


def save_bt_yaml(bt_dict, path):
    with open(path, 'w', encoding='utf-8') as f:
        yaml.dump(bt_dict, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


# ============================================================
# Parameter Sampling (bt_optimizer.py와 동일 알고리즘)
# ============================================================

def latin_hypercube_sample(n_samples, rng=None):
    if rng is None:
        rng = np.random.default_rng()

    continuous_params = [(name, spec) for name, spec in PARAM_SPACE.items()
                         if spec["type"] == "continuous"]
    discrete_params = [(name, spec) for name, spec in PARAM_SPACE.items()
                       if spec["type"] == "discrete"]

    n_cont = len(continuous_params)
    lhs_matrix = np.zeros((n_samples, n_cont))
    for j in range(n_cont):
        perm = rng.permutation(n_samples)
        for i in range(n_samples):
            lhs_matrix[i, j] = (perm[i] + rng.random()) / n_samples

    samples = []
    for i in range(n_samples):
        params = {}
        for j, (name, spec) in enumerate(continuous_params):
            lo, hi = spec["range"]
            params[name] = float(lo + (hi - lo) * lhs_matrix[i, j])
        for name, spec in discrete_params:
            idx = int(rng.integers(0, len(spec["choices"])))
            params[name] = spec["choices"][idx]
        samples.append(params)
    return samples


def perturb_params(params, scale=0.15, rng=None):
    if rng is None:
        rng = np.random.default_rng()
    new_params = deepcopy(params)
    for name, spec in PARAM_SPACE.items():
        if spec["type"] == "continuous":
            lo, hi = spec["range"]
            delta = (hi - lo) * scale * rng.normal()
            new_params[name] = float(np.clip(params[name] + delta, lo, hi))
        elif spec["type"] == "discrete":
            if rng.random() < 0.15:
                idx = int(rng.integers(0, len(spec["choices"])))
                new_params[name] = spec["choices"][idx]
    return new_params


# ============================================================
# Fitness Evaluation
# ============================================================

def compute_match_score(winner, our_hp, their_hp):
    hp_diff = float(our_hp - their_hp) / 100.0
    hp_diff = max(-1.0, min(1.0, hp_diff))
    if winner == "tree1":
        return WIN_BASE + hp_diff * HP_WEIGHT
    elif winner == "draw":
        return DRAW_BASE + hp_diff * HP_WEIGHT
    else:
        return LOSS_BASE + hp_diff * HP_WEIGHT


def evaluate_fitness(params, rounds_per_opponent=1, worker_id=None, verbose=False):
    from src.match.runner import BehaviorTreeMatch
    from scripts.run_match import get_tree_path

    bt_dict = generate_bt_yaml(params)
    suffix = f"_{worker_id}" if worker_id is not None else f"_{os.getpid()}"
    temp_dir = PROJECT_ROOT / "logs" / "temp_bt"
    temp_dir.mkdir(parents=True, exist_ok=True)

    temp_path = temp_dir / f"_temp_cityhunter{suffix}.yaml"
    save_bt_yaml(bt_dict, temp_path)

    total_score = 0.0
    details = {}

    for opponent in OPPONENTS:
        try:
            tree2_path = get_tree_path(opponent)
        except FileNotFoundError as e:
            if verbose:
                print(f"  Opponent not found {opponent}: {e}")
            details[opponent] = {
                "wins": 0, "draws": 0, "losses": rounds_per_opponent,
                "score": LOSS_BASE * rounds_per_opponent,
                "avg_hp_diff": 0.0,
            }
            total_score += LOSS_BASE * rounds_per_opponent
            continue

        opp_score = 0.0
        total_hp_diff = 0.0
        wins = draws = 0

        for _ in range(rounds_per_opponent):
            try:
                # tree1_name="cityhunter" so MatchCore loads nodes from
                # submissions/cityhunter/nodes/ regardless of temp file path
                match = BehaviorTreeMatch(
                    tree1_file=str(temp_path),
                    tree2_file=tree2_path,
                    config_name="1v1/NoWeapon/bt_vs_bt",
                    max_steps=1500,
                    tree1_name="cityhunter",
                    tree2_name=opponent,
                )
                result = match.run(verbose=False)
                tree1_reward = getattr(result, "tree1_reward", 0.0)
                tree2_reward = getattr(result, "tree2_reward", 0.0)
                winner = getattr(result, "winner", "unknown")

                our_hp   = 100.0 + tree1_reward
                their_hp = 100.0 + tree2_reward
                opp_score    += compute_match_score(winner, our_hp, their_hp)
                total_hp_diff += (our_hp - their_hp)
                if winner == "tree1":
                    wins += 1
                elif winner == "draw":
                    draws += 1
            except Exception as e:
                opp_score += LOSS_BASE
                if verbose:
                    print(f"  Error round vs {opponent}: {e}")

        losses = rounds_per_opponent - wins - draws
        avg_hp_diff = total_hp_diff / max(1, rounds_per_opponent)
        total_score += opp_score

        details[opponent] = {
            "wins": wins, "draws": draws, "losses": losses,
            "score": round(opp_score, 2),
            "avg_hp_diff": round(avg_hp_diff, 1),
        }

    try:
        temp_path.unlink()
    except Exception:
        pass

    return total_score, details


def _eval_worker(args):
    idx, params, rounds_per_opponent = args
    score, details = evaluate_fitness(params, rounds_per_opponent=rounds_per_opponent)
    return idx, score, details


# ============================================================
# Search
# ============================================================

def run_search(n_candidates=50, n_refine_neighbors=7, n_workers=None, seed=42):
    """
    Stage 1: LHS (n_candidates, 2 rounds, parallel)
    Stage 2: Top-10 refinement (n_refine_neighbors, 3 rounds, parallel)
    Stage 3: Top-5 validation (5 rounds, sequential)
    """
    if n_workers is None:
        n_workers = max(1, mp.cpu_count() - 1)

    run_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    rng = np.random.default_rng(seed)
    total_start = time.time()

    # ── 베이스라인 시드: v3.2-opt 최적 파라미터 + v4.1 신규 기본값 ──
    baseline = {
        # L1 — v3.2-opt 값
        "hard_deck_ft":        592.0,
        "climb_target_ft":     2371.0,
        # L2 — v3.2-opt 값
        "wez_dist_ft":          693.0,
        "wez_los_deg":            5.3,
        # L5 LagPreempt — v3.2-opt 값
        "lag_closure_kts":      307.0,
        "lag_dist_ft":          2691.0,
        # L6 공세 진입 — v3.2-opt 값
        "off_ata_deg":           65.3,
        "off_dist_ft":         10213.0,
        # L6-1 WEZ — v3.2-opt 값
        "gun_dist_ft":          2396.0,
        "gun_ata_deg":             6.7,
        # L6-1b 써클파이트 — 교범 기준값 (4000ft 근거리)
        "circle_fight_dist_ft": 4000.0,
        # L7 수세 진입 — v3.2-opt 값
        "def_threat_aa_deg":    119.0,
        "def_dist_ft":          3000.0,  # v3.2-opt 복원 (eagle1 대응 기준)
        "def_break_aa_deg":     121.0,
        "def_maneuver_aa_deg":   80.0,
        # L7-4 HighYoYo — 교범 기준값
        "vel_high_kts":          500.0,
        "highy_merge_dist_ft":  1640.0,
        # L8 PNPursuit — v3.2-opt 값
        "pn_close_dist_ft":     5217.0,
    }

    # ── Stage 1 ──
    print(f"\n{'='*60}")
    print(f"  Stage 1: LHS Exploration ({n_candidates} candidates, 2 rounds)")
    print(f"  Workers: {n_workers}  |  BT: v4.1 ({len(PARAM_SPACE)} params)")
    print(f"{'='*60}\n")

    candidates = latin_hypercube_sample(n_candidates, rng)
    candidates.insert(0, baseline)  # 베이스라인 항상 포함

    work_items = [(i, p, 2) for i, p in enumerate(candidates)]
    explore_results = [None] * len(candidates)

    stage1_start = time.time()
    with mp.Pool(processes=n_workers) as pool:
        for idx, score, details in pool.imap_unordered(_eval_worker, work_items):
            explore_results[idx] = {"params": candidates[idx], "score": score, "details": details}
            wins   = sum(d["wins"]   for d in details.values())
            draws  = sum(d["draws"]  for d in details.values())
            losses = sum(d["losses"] for d in details.values())
            done = sum(1 for r in explore_results if r is not None)
            print(f"  [{done:3d}/{len(candidates)}] #{idx+1} score={score:7.2f}  "
                  f"W/D/L={wins}/{draws}/{losses}", flush=True)

    stage1_elapsed = time.time() - stage1_start
    explore_results = [r for r in explore_results if r is not None]
    explore_results.sort(key=lambda x: x["score"], reverse=True)
    print(f"\n  Stage 1 done in {stage1_elapsed/60:.1f}min. Best: {explore_results[0]['score']:.2f}")

    # ── Stage 2 ──
    top_k = 10
    print(f"\n{'='*60}")
    print(f"  Stage 2: Refine Top-{top_k} ({n_refine_neighbors} neighbors, 5 rounds)")
    print(f"{'='*60}\n")

    refine_candidates = []
    for ci in range(min(top_k, len(explore_results))):
        base = explore_results[ci]["params"]
        refine_candidates.append(base)
        for _ in range(n_refine_neighbors):
            refine_candidates.append(perturb_params(base, scale=0.12, rng=rng))

    work_items = [(i, p, 5) for i, p in enumerate(refine_candidates)]
    refine_results = [None] * len(refine_candidates)

    stage2_start = time.time()
    with mp.Pool(processes=n_workers) as pool:
        for idx, score, details in pool.imap_unordered(_eval_worker, work_items):
            refine_results[idx] = {"params": refine_candidates[idx], "score": score, "details": details}
            wins   = sum(d["wins"]   for d in details.values())
            draws  = sum(d["draws"]  for d in details.values())
            losses = 20 - wins - draws  # 4 opponents × 5 rounds
            done = sum(1 for r in refine_results if r is not None)
            print(f"  [{done:3d}/{len(refine_candidates)}] score={score:7.2f}  "
                  f"W/D/L={wins}/{draws}/{losses}", flush=True)

    stage2_elapsed = time.time() - stage2_start
    refine_results = [r for r in refine_results if r is not None]
    refine_results.sort(key=lambda x: x["score"], reverse=True)
    print(f"\n  Stage 2 done in {stage2_elapsed/60:.1f}min. Best: {refine_results[0]['score']:.2f}")

    # ── Stage 3: Validate Top-5 ──
    top_validate = 5
    stage3_rounds = 10
    print(f"\n{'='*60}")
    print(f"  Stage 3: Validate Top-{top_validate} ({stage3_rounds} rounds each)")
    print(f"{'='*60}\n")

    final_results = []
    for vi in range(min(top_validate, len(refine_results))):
        params = refine_results[vi]["params"]
        score, details = evaluate_fitness(params, rounds_per_opponent=stage3_rounds)
        final_results.append({"params": params, "score": score, "details": details})

        n_total = stage3_rounds * len(OPPONENTS)
        wins   = sum(d["wins"]   for d in details.values())
        draws  = sum(d["draws"]  for d in details.values())
        losses = n_total - wins - draws
        print(f"  #{vi+1}: score={score:.2f}  W/D/L={wins}/{draws}/{losses}")
        for opp, d in details.items():
            w, dr, lo = d["wins"], d["draws"], d["losses"]
            hp = d.get("avg_hp_diff", 0)
            tag = "W" if w > lo else ("D" if w == lo and dr > 0 else "L")
            print(f"      vs {opp:12s}: {w}W {dr}D {lo}L  hp_diff={hp:+.0f}  [{tag}]")

    final_results.sort(key=lambda x: x["score"], reverse=True)

    total_elapsed = time.time() - total_start
    print(f"\n{'='*60}")
    print(f"  Search Complete! ({total_elapsed/60:.1f} min)")
    print(f"  Best score: {final_results[0]['score']:.2f}")
    print(f"{'='*60}\n")

    # ── Save ──
    logs_dir = PROJECT_ROOT / "logs" / "cityhunter"
    logs_dir.mkdir(parents=True, exist_ok=True)
    serializable = [{"score": r["score"], "params": r["params"], "details": r["details"]}
                    for r in final_results]

    ts_path     = logs_dir / f"opt_results_{run_ts}.json"
    latest_path = logs_dir / "opt_results.json"
    for path in [ts_path, latest_path]:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(serializable, f, indent=2, default=str)

    best_params = final_results[0]["params"]
    best_yaml_path = logs_dir / "best_bt.yaml"
    save_bt_yaml(generate_bt_yaml(best_params), best_yaml_path)

    params_path = logs_dir / "best_params.json"
    with open(params_path, 'w', encoding='utf-8') as f:
        json.dump(best_params, f, indent=2, default=str)

    print(f"  Results  : {latest_path}")
    print(f"  Best BT  : {best_yaml_path}")
    print(f"  Best Params: {params_path}")

    return final_results


def run_validation(rounds=5):
    """저장된 최적 파라미터로 cityhunter 재검증."""
    logs_dir = PROJECT_ROOT / "logs" / "cityhunter"
    results_path = logs_dir / "opt_results.json"
    if not results_path.exists():
        print("No optimization results found. Run optimizer first.")
        return

    with open(results_path, encoding='utf-8') as f:
        data = json.load(f)

    best_params = data[0]["params"]
    print(f"\nValidation ({rounds} rounds per opponent)")
    print(f"Previous score: {data[0]['score']:.2f}\n")

    score, details = evaluate_fitness(best_params, rounds_per_opponent=rounds, verbose=True)

    total_wins   = sum(d["wins"]   for d in details.values())
    total_draws  = sum(d["draws"]  for d in details.values())
    total_losses = sum(d["losses"] for d in details.values())

    print(f"\nValidation Results: {total_wins}W {total_draws}D {total_losses}L  score={score:.2f}")
    for opp, d in details.items():
        hp = d.get("avg_hp_diff", 0)
        print(f"  vs {opp:12s}: {d['wins']}W {d['draws']}D {d['losses']}L  hp={hp:+.1f}")

    # best_bt.yaml 갱신
    best_yaml_path = logs_dir / "best_bt.yaml"
    save_bt_yaml(generate_bt_yaml(best_params), best_yaml_path)
    print(f"\nSaved best BT to: {best_yaml_path}")
    print(f"Params: {json.dumps(best_params, indent=2, default=str)}")


# ============================================================
# Entry Point
# ============================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="BT Optimizer — cityhunter")
    parser.add_argument("--candidates", type=int, default=50,
                        help="Stage 1 LHS candidate count (default: 50)")
    parser.add_argument("--workers", type=int, default=None,
                        help="Parallel workers (default: cpu_count-1)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--validate", action="store_true",
                        help="Validate saved best params instead of running search")
    parser.add_argument("--rounds", type=int, default=5,
                        help="Rounds per opponent for --validate (default: 5)")
    args = parser.parse_args()

    if args.validate:
        run_validation(rounds=args.rounds)
    else:
        run_search(
            n_candidates=args.candidates,
            n_workers=args.workers,
            seed=args.seed,
        )
