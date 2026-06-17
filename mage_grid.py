"""
Mage Grid — Pygame implementation with event-based combat animation.

Board: 9 cols × 10 rows. frontier[c] = last row owned by TOP player.
Rows 0..frontier[c] → top territory (dark brown, hollow units).
Rows frontier[c]+1..9 → bottom territory (light brown, filled units).

CombatPlan is computed once before animation starts and drives the
AnimState machine. No board-snapping shortcuts.
"""

import pygame
import sys
import random
import math
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional

# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------

GRID_COLS = 9
GRID_ROWS = 10
STARTING_FRONTIER = 4          # rows 0-4 top, 5-9 bottom

CELL = 62
SIDEBAR_W = 230
HUD_H = 72
PLAYER_BAR_H = 28               # strip above/below grid for name + HP
GRID_Y = HUD_H + PLAYER_BAR_H  # pixel y where the grid starts
SCREEN_W = GRID_COLS * CELL + SIDEBAR_W
SCREEN_H = GRID_Y + GRID_ROWS * CELL + PLAYER_BAR_H
FPS = 60

# ---------------------------------------------------------------------------
# Color / unit definitions
# ---------------------------------------------------------------------------

COLOR_NAMES = ["Blue",  "Red",    "Yellow", "Green", "White"]
COLOR_KEYS  = ["B",     "R",      "Y",      "G",     "W"]
NUM_COLORS  = 5
HP_MAX      = 10

UNIT_RGB = {
    0: (90,  150, 255),   # Blue
    1: (255,  60,  45),   # Red
    2: (255, 225,  30),   # Yellow
    3: ( 50, 195,  75),   # Green
    4: (215, 215, 215),   # White
}

BG_TOP       = (55,  32,  12)
BG_BOTTOM    = (172, 133,  78)
BG_SIDEBAR   = (28,  22,  16)
BG_HUD       = (18,  15,  10)
C_GRID       = (0,   0,   0)
C_FRONTIER   = (255, 255, 255)
C_DEF_NORMAL = (255, 255, 255)   # defense outline before hit
C_DEF_HIT    = (255,  40,  40)   # defense outline after hit


# ---------------------------------------------------------------------------
# Unit shapes
# ---------------------------------------------------------------------------

def _surf(w, h):
    s = pygame.Surface((w, h), pygame.SRCALPHA)
    return s

def draw_unit(dest, color_idx, rect, filled, alpha=255, anim_t=0.0):
    color = (*UNIT_RGB[color_idx], alpha)
    fns = [_draw_droplet, _draw_flame, _draw_bolt, _draw_tree, _draw_rock]
    fns[color_idx](dest, color, rect, filled, anim_t)


def _poly(s, color, pts, filled, lw=3):
    pygame.draw.polygon(s, color, pts, 0 if filled else lw)


def _draw_droplet(dest, color, rect, filled, anim_t=0.0):
    """Smooth teardrop that bobs gently when animated."""
    s = _surf(rect.w, rect.h)
    cx, cy = rect.w // 2, rect.h // 2
    r   = rect.w * 0.295
    bob = math.sin(anim_t * 2.6) * rect.h * 0.07  # slow vertical bob
    bcy = cy + rect.h * 0.09 + bob
    tip = (cx, int(cy - rect.h * 0.42 + bob))

    pts = []
    for i in range(20):
        a = math.radians(32 - i * (244 / 19))
        pts.append((int(cx + r * math.cos(a)),
                    int(bcy - r * math.sin(a))))
    pts.append(tip)

    _poly(s, color, pts, filled)
    dest.blit(s, rect.topleft)


def _draw_flame(dest, color, rect, filled, anim_t=0.0):
    """Flame whose tips flicker at different rates for an organic fire look."""
    s = _surf(rect.w, rect.h)
    cx, cy = rect.w // 2, rect.h // 2
    w, h = rect.w * 0.44, rect.h * 0.46
    # Independent oscillators for each flame tongue
    tip_sx  = math.sin(anim_t * 7.0) * w * 0.10   # main tip sways side
    tip_sy  = math.sin(anim_t * 5.3) * h * 0.05   # main tip bobs
    rt_sx   = math.sin(anim_t * 9.1 + 1.5) * w * 0.08   # right tongue
    lt_sx   = math.sin(anim_t * 8.5 + 0.8) * w * 0.08   # left tongue (different phase)
    pts = [
        (cx + tip_sx,                        cy - h + tip_sy),       # main tip
        (cx + w * 0.14,                      cy - h * 0.46),
        (cx + w * 0.46 + rt_sx,              cy - h * 0.64),         # right tongue tip
        (cx + w * 0.58,                      cy - h * 0.26),
        (cx + w * 0.72,                      cy + h * 0.14),
        (cx + w * 0.54,                      cy + h * 0.64),
        (cx + w * 0.22,                      cy + h),                # base (stable)
        (cx - w * 0.22,                      cy + h),
        (cx - w * 0.54,                      cy + h * 0.64),
        (cx - w * 0.72,                      cy + h * 0.14),
        (cx - w * 0.58,                      cy - h * 0.26),
        (cx - w * 0.46 + lt_sx,              cy - h * 0.64),         # left tongue tip
        (cx - w * 0.14,                      cy - h * 0.46),
    ]
    pts = [(int(x), int(y)) for x, y in pts]
    _poly(s, color, pts, filled)
    dest.blit(s, rect.topleft)


def _draw_bolt(dest, color, rect, filled, anim_t=0.0):
    """Lightning bolt that rapidly flickers its brightness."""
    s = _surf(rect.w, rect.h)
    cx, cy = rect.w // 2, rect.h // 2
    w, h = rect.w * 0.22, rect.h * 0.40
    # Flicker: brightness pulses fast with occasional near-off moments
    flicker = 0.55 + 0.45 * abs(math.sin(anim_t * 14.0))
    r, g, b, a = color
    color = (r, g, b, int(a * flicker))
    pts = [
        (cx + w * 0.55,  cy - h),
        (cx - w * 0.10,  cy - h * 0.05),
        (cx + w * 0.65,  cy - h * 0.05),
        (cx - w * 0.55,  cy + h),
        (cx + w * 0.10,  cy + h * 0.05),
        (cx - w * 0.65,  cy + h * 0.05),
    ]
    pts = [(int(x), int(y)) for x, y in pts]
    _poly(s, color, pts, filled)
    dest.blit(s, rect.topleft)


def _draw_tree(dest, color, rect, filled, anim_t=0.0):
    """Pine tree that sways its canopy in a gentle breeze."""
    s = _surf(rect.w, rect.h)
    cx, cy = rect.w // 2, rect.h // 2
    w, h = rect.w * 0.40, rect.h * 0.44
    tw = max(3, int(rect.w * 0.12))
    th = int(rect.h * 0.20)
    sway     = math.sin(anim_t * 1.4) * w * 0.20   # tip sways most
    sway_low = sway * 0.35                           # base of canopy sways less
    canopy = [
        (cx + sway,           int(cy - h)),
        (int(cx + w + sway_low), int(cy + h * 0.30)),
        (int(cx - w + sway_low), int(cy + h * 0.30)),
    ]
    tx = int(cx - tw // 2 + sway_low * 0.5)
    ty = int(cy + h * 0.30)
    if filled:
        pygame.draw.polygon(s, color, canopy)
        pygame.draw.rect(s, color, (tx, ty, tw, th))
    else:
        pygame.draw.polygon(s, color, canopy, 3)
        pygame.draw.rect(s, color, (tx, ty, tw, th), 2)
    dest.blit(s, rect.topleft)


def _draw_rock(dest, color, rect, filled, anim_t=0.0):
    """Rounded boulder with a subtle breathing pulse."""
    s = _surf(rect.w, rect.h)
    cx, cy = rect.w // 2, rect.h // 2
    w, h = rect.w * 0.42, rect.h * 0.40
    sc = 1.0 + math.sin(anim_t * 0.9) * 0.035   # gentle scale pulse
    raw = [
        (cx + w * 0.20,  cy - h),
        (cx + w * 0.75,  cy - h * 0.58),
        (cx + w,         cy + h * 0.12),
        (cx + w * 0.72,  cy + h * 0.75),
        (cx + w * 0.15,  cy + h),
        (cx - w * 0.38,  cy + h * 0.90),
        (cx - w,         cy + h * 0.38),
        (cx - w * 0.80,  cy - h * 0.32),
        (cx - w * 0.35,  cy - h * 0.88),
    ]
    pts = [(int(cx + (x - cx) * sc), int(cy + (y - cy) * sc)) for x, y in raw]
    _poly(s, color, pts, filled)
    dest.blit(s, rect.topleft)


def cell_rect(r, c):
    return pygame.Rect(c * CELL, r * CELL + GRID_Y, CELL, CELL)


# ---------------------------------------------------------------------------
# Board state
# ---------------------------------------------------------------------------

@dataclass
class BoardState:
    board: list      # board[r][c] = color index 0-4
    frontier: list   # frontier[c] = last row owned by top player
    hp_top: int = HP_MAX
    hp_bottom: int = HP_MAX

    def copy(self):
        return BoardState(
            [row[:] for row in self.board],
            self.frontier[:],
            self.hp_top,
            self.hp_bottom,
        )

    def owner_is_top(self, r, c):
        return r <= self.frontier[c]


def make_board():
    board = [[random.randrange(NUM_COLORS) for _ in range(GRID_COLS)]
             for _ in range(GRID_ROWS)]
    return BoardState(board=board, frontier=[STARTING_FRONTIER] * GRID_COLS)


# ---------------------------------------------------------------------------
# Formation detection
# ---------------------------------------------------------------------------
# NOTE: rows[0] = FRONT soldier (closest to enemy frontier).
#       rows[-1] = BACK soldier (furthest from enemy).
# ---------------------------------------------------------------------------

@dataclass
class AttackFormation:
    formation_id: int
    col: int
    rows: list          # rows[0]=front, rows[-1]=back

    @property
    def power(self):    # 3 soldiers → 1 kill, 4 → 2, etc.
        return len(self.rows) - 2

    def front_row(self):
        return self.rows[0]


@dataclass
class DefenseFormation:
    formation_id: int
    cells: list         # [(r,c), ...]

    def cell_set(self):
        return set(self.cells)


def find_attack_formations(bs: BoardState, attacker_is_top: bool,
                           chosen: tuple) -> list:
    """Vertical runs ≥3 in attacker territory, all colors in chosen."""
    out, fid = [], 0
    for c in range(GRID_COLS):
        lo, hi = (0, bs.frontier[c]) if attacker_is_top \
                 else (bs.frontier[c]+1, GRID_ROWS-1)
        if lo > hi:
            continue
        run = []
        for r in range(lo, hi+1):
            if bs.board[r][c] in chosen:
                run.append(r)
            else:
                if len(run) >= 3:
                    # rows[0]=front: for top attacker, front=highest row; reverse the low→high run
                    ordered = list(reversed(run)) if attacker_is_top else run[:]
                    out.append(AttackFormation(fid, c, ordered))
                    fid += 1
                run = []
        if len(run) >= 3:
            ordered = list(reversed(run)) if attacker_is_top else run[:]
            out.append(AttackFormation(fid, c, ordered))
            fid += 1
    return out


def find_defense_formations(bs: BoardState, defender_is_top: bool,
                            chosen: tuple) -> list:
    """Horizontal runs ≥3 in defender territory, all colors in chosen."""
    out, fid = [], 0
    for r in range(GRID_ROWS):
        run = []
        for c in range(GRID_COLS):
            ok = (bs.owner_is_top(r, c) == defender_is_top) and \
                 (bs.board[r][c] in chosen)
            if ok:
                run.append(c)
            else:
                if len(run) >= 3:
                    out.append(DefenseFormation(fid, [(r, cc) for cc in run]))
                    fid += 1
                run = []
        if len(run) >= 3:
            out.append(DefenseFormation(fid, [(r, cc) for cc in run]))
            fid += 1
    return out


# ---------------------------------------------------------------------------
# Formation animation info (precomputed pixel events)
# ---------------------------------------------------------------------------

@dataclass
class FmtAnimInfo:
    """Per-formation animation data, built during combat plan construction."""
    fmt_idx: int
    col: int
    initial_rows: list      # rows[0]=front
    attacker_is_top: bool
    frontier_at_start: int  # frontier[col] when this formation begins
    kills: int
    killed_def_rows: list   # defender rows killed, in order
    blocked_by: Optional[int]   # defense formation id, or None
    breakthrough: bool

    # Derived pixel thresholds (set in __post_init__)
    frontier_cross_px: float = field(init=False)
    kill_px: list = field(init=False)      # kill_px[k] = px when kill k triggers
    end_px: float = field(init=False)

    def __post_init__(self):
        front = self.initial_rows[0]
        f = self.frontier_at_start
        if self.attacker_is_top:
            # front soldier is at row `front`, frontier boundary below row f
            # distance in cells = f - front + 1  (always ≥1 since front ≤ f)
            cells_to_frontier = f - front + 1
        else:
            # front soldier at row `front`, frontier boundary above row f+1
            # distance in cells = front - (f+1) + 1 = front - f
            cells_to_frontier = front - f
        self.frontier_cross_px = cells_to_frontier * CELL

        # After the front-2 dissolve at the frontier, the next remaining soldier
        # is 2 cells behind the first defender.  Each subsequent kill also leaves
        # the next soldier 2 cells from the new defender.  Hence each event is
        # spaced 2*CELL apart, starting at frontier_cross_px + 2*CELL.
        self.kill_px = [self.frontier_cross_px + 2*(k+1)*CELL
                        for k in range(self.kills)]

        power = len(self.initial_rows) - 2
        has_remaining_soldiers = self.kills < power   # some soldiers still alive after all kills

        if self.blocked_by is not None or (self.breakthrough and has_remaining_soldiers):
            # Remaining soldiers slide 2 more cells to reach the defense / edge.
            self.end_px = self.frontier_cross_px + 2*(self.kills+1)*CELL
        elif self.kills > 0:
            # All soldiers consumed by kills (or breakthrough triggered on the
            # last kill): animation ends when the last kill fires.
            self.end_px = self.kill_px[-1]
        else:
            self.end_px = self.frontier_cross_px

    def soldiers_at(self, slide_px: float) -> int:
        """How many soldiers are still present at this slide offset."""
        n = len(self.initial_rows)
        if slide_px >= self.end_px and \
                (self.blocked_by is not None or self.breakthrough or self.kills == n-2):
            return 0
        if slide_px < self.frontier_cross_px:
            return n
        removed = 2
        for px in self.kill_px:
            if slide_px >= px:
                removed += 1
        return max(0, n - removed)

    def defender_kills_at(self, slide_px: float) -> list:
        """Defender rows killed so far at this slide offset."""
        return [row for px, row in zip(self.kill_px, self.killed_def_rows)
                if slide_px >= px]


# ---------------------------------------------------------------------------
# Combat Plan
# ---------------------------------------------------------------------------

@dataclass
class CombatPlan:
    attacker_is_top: bool
    # sorted execution order: left→right, front-to-back within column
    attack_formations: list
    defense_formations: list
    defense_map: dict           # fid → DefenseFormation
    fmt_anim: list              # FmtAnimInfo per formation (same order)
    hit_def_ids: set
    breakthrough_cols: list
    # Final board after all combat
    final_board: BoardState
    # Non-formation attacker cells (phase 1 fade-out, phase 5 fade-back-in)
    non_fmt_atk_cells: list
    # Slide fill maps: original (r,c) → final (r2,c2) for surviving movers
    atk_slide_map: dict
    def_slide_map: dict
    # Spawn cells (phase 8 fade-in), taken from final_board
    atk_spawn_cells: list
    def_spawn_cells: list


# ---------------------------------------------------------------------------
# Combat plan builder
# ---------------------------------------------------------------------------

def build_combat_plan(bs: BoardState, attacker_is_top: bool,
                      atk_colors: tuple, def_colors: tuple) -> CombatPlan:
    """
    Fully simulate combat on a copy of bs. Record every event needed to
    drive animation. Never modify bs.
    """
    defender_is_top = not attacker_is_top
    orig = bs.copy()   # reference snapshot; never modify

    atk_fmts = find_attack_formations(orig, attacker_is_top, atk_colors)
    def_fmts  = find_defense_formations(orig, defender_is_top, def_colors)
    def_map   = {f.formation_id: f for f in def_fmts}

    # Cell → defense formation id (for fast lookup during simulation)
    cell_to_dfid: dict = {}
    for df in def_fmts:
        for cell in df.cells:
            cell_to_dfid[cell] = df.formation_id

    # Sort attack formations: left→right column, then front row closest to enemy first
    # (for top attacker, front = highest row → smallest distance to frontier → largest row
    #  so sort by row descending within column; for bottom, sort by row ascending)
    def sort_key(af: AttackFormation):
        front = af.rows[0]
        return (af.col, -front if attacker_is_top else front)

    atk_fmts_sorted = sorted(atk_fmts, key=sort_key)

    # Non-formation attacker cells
    fmt_cell_set: set = set()
    for af in atk_fmts_sorted:
        for r in af.rows:
            fmt_cell_set.add((r, af.col))

    if attacker_is_top:
        non_fmt_atk = [(r, c) for r in range(GRID_ROWS) for c in range(GRID_COLS)
                       if orig.owner_is_top(r, c) and (r, c) not in fmt_cell_set]
    else:
        non_fmt_atk = [(r, c) for r in range(GRID_ROWS) for c in range(GRID_COLS)
                       if not orig.owner_is_top(r, c) and (r, c) not in fmt_cell_set]

    # Simulate combat; track frontier per column.
    # Reclaims from breakthroughs are DEFERRED until every formation in the
    # same column has finished.  This means a back formation sees the frontier
    # at the back-line set by the front formation, so it also breaks through
    # automatically (no defenders left), earning an additional damage point.
    sim_frontier = orig.frontier[:]
    hit_def_ids: set = set()
    breakthrough_cols: list = []
    fmt_anim_list: list = []
    col_breakthrough_count: dict = {}   # col → breakthroughs, reclaim deferred

    # All attacker formation cells are consumed (removed from board)
    atk_removed: set = set(fmt_cell_set)
    # Defender cells removed: killed individually + all cells in hit defense formations
    def_removed: set = set()

    for idx, af in enumerate(atk_fmts_sorted):
        c = af.col
        frontier_at_start = sim_frontier[c]
        remaining_power   = af.power
        killed_def_rows: list = []
        blocked_by: Optional[int] = None
        breakthrough = False

        while remaining_power > 0:
            if attacker_is_top:
                next_def_row = sim_frontier[c] + 1
                past_edge    = next_def_row >= GRID_ROWS
            else:
                next_def_row = sim_frontier[c]
                past_edge    = next_def_row < 0

            if past_edge:
                breakthrough = True
                break

            cell = (next_def_row, c)
            if cell in cell_to_dfid:
                did = cell_to_dfid[cell]
                hit_def_ids.add(did)
                blocked_by = did
                break
            else:
                killed_def_rows.append(next_def_row)
                def_removed.add(cell)
                sim_frontier[c] += 1 if attacker_is_top else -1
                remaining_power -= 1

        if breakthrough:
            # Count but do NOT reclaim yet — leave frontier at back line so
            # subsequent formations in this column also break through.
            col_breakthrough_count[c] = col_breakthrough_count.get(c, 0) + 1

        fmt_anim_list.append(FmtAnimInfo(
            fmt_idx=idx, col=c,
            initial_rows=af.rows[:],
            attacker_is_top=attacker_is_top,
            frontier_at_start=frontier_at_start,
            kills=len(killed_def_rows),
            killed_def_rows=killed_def_rows,
            blocked_by=blocked_by,
            breakthrough=breakthrough,
        ))

    # Apply all deferred reclaims now that every formation has processed.
    # Each breakthrough costs the defender 3 squares and 1 HP.
    for c, count in col_breakthrough_count.items():
        for _ in range(count):
            breakthrough_cols.append(c)
            if attacker_is_top:
                sim_frontier[c] = max(sim_frontier[c] - 3, -1)
            else:
                sim_frontier[c] = min(sim_frontier[c] + 3, GRID_ROWS - 1)

    # Add all cells in hit defense formations to def_removed
    for did in hit_def_ids:
        for cell in def_map[did].cells:
            def_removed.add(cell)

    # Cells in reclaimed zones (were attacker territory, now defender territory)
    # must be treated as forced defender spawns, not surviving attackers.
    reclaimed_cells: set = set()
    for c in breakthrough_cols:
        orig_f = orig.frontier[c]
        new_f  = sim_frontier[c]
        if attacker_is_top:
            # top attacked; defender reclaimed rows new_f+1..orig_f
            # (orig_f is where frontier was before formation started in this col;
            #  but the breakthrough may have consumed all defenders, then reclaimed back)
            # rows now in bottom territory that were originally top territory:
            for r in range(new_f + 1, orig_f + 1):
                reclaimed_cells.add((r, c))
        else:
            # bottom attacked; defender reclaimed rows orig_f+1..new_f
            for r in range(orig_f + 1, new_f + 1):
                reclaimed_cells.add((r, c))

    # Build final board
    final = orig.copy()
    final.frontier = sim_frontier[:]
    if attacker_is_top:
        final.hp_bottom -= len(breakthrough_cols)
    else:
        final.hp_top -= len(breakthrough_cols)

    # --- Attacker gravity ---
    atk_slide_map: dict = {}
    atk_spawn_cells: list = []
    affected_atk_cols = set(af.col for af in atk_fmts_sorted)

    for c in affected_atk_cols:
        f = sim_frontier[c]

        # Search the ORIGINAL attacker territory for survivors, not the new
        # territory.  This captures non-formation units sitting in the reclaimed
        # zone — they get pushed backward to the front of the new territory
        # rather than disappearing.
        if attacker_is_top:
            orig_atk_rows = list(range(0, orig.frontier[c] + 1))
            new_territory  = list(range(0, f + 1))
        else:
            orig_atk_rows = list(range(orig.frontier[c] + 1, GRID_ROWS))
            new_territory  = list(range(f + 1, GRID_ROWS))

        survivors = [r for r in orig_atk_rows
                     if (r, c) not in atk_removed
                     and (r, c) not in def_removed]
        # reclaimed_cells exclusion removed: those units slide back to the front.

        # Clamp in case heavy reclaiming leaves fewer slots than survivors.
        n = min(len(survivors), len(new_territory))
        survivors = survivors[-n:] if attacker_is_top else survivors[:n]

        if attacker_is_top:
            # Fill slots closest to the frontier first (highest rows).
            # The survivor with the highest original row → highest new row.
            new_pos = new_territory[-n:]
            for old_r, new_r in zip(reversed(survivors), reversed(new_pos)):
                atk_slide_map[(old_r, c)] = (new_r, c)
                final.board[new_r][c] = orig.board[old_r][c]
            for r in new_territory[:-n] if n else new_territory:
                final.board[r][c] = random.randrange(NUM_COLORS)
                atk_spawn_cells.append((r, c))
        else:
            new_pos = new_territory[:n]
            for old_r, new_r in zip(survivors, new_pos):
                atk_slide_map[(old_r, c)] = (new_r, c)
                final.board[new_r][c] = orig.board[old_r][c]
            for r in new_territory[n:]:
                final.board[r][c] = random.randrange(NUM_COLORS)
                atk_spawn_cells.append((r, c))

    # --- Defender gravity ---
    def_slide_map: dict = {}
    def_spawn_cells: list = []
    affected_def_cols = set(c for (r, c) in def_removed) | \
                        set(c for (r, c) in reclaimed_cells)

    for c in affected_def_cols:
        f = sim_frontier[c]
        if defender_is_top:
            region = list(range(0, f + 1))
        else:
            region = list(range(f + 1, GRID_ROWS))

        # Survivors: originally in defender territory, not removed
        if defender_is_top:
            orig_def_region = list(range(0, orig.frontier[c] + 1))
        else:
            orig_def_region = list(range(orig.frontier[c] + 1, GRID_ROWS))

        survivors = [r for r in orig_def_region
                     if (r, c) not in def_removed and r in region]

        if defender_is_top:
            new_pos = list(range(f - len(survivors) + 1, f + 1))
            for old_r, new_r in zip(reversed(survivors), reversed(new_pos)):
                def_slide_map[(old_r, c)] = (new_r, c)
                final.board[new_r][c] = orig.board[old_r][c]
            for r in range(0, f - len(survivors) + 1):
                final.board[r][c] = random.randrange(NUM_COLORS)
                def_spawn_cells.append((r, c))
        else:
            new_pos = list(range(f + 1, f + 1 + len(survivors)))
            for old_r, new_r in zip(survivors, new_pos):
                def_slide_map[(old_r, c)] = (new_r, c)
                final.board[new_r][c] = orig.board[old_r][c]
            for r in range(f + 1 + len(survivors), GRID_ROWS):
                final.board[r][c] = random.randrange(NUM_COLORS)
                def_spawn_cells.append((r, c))

    # Reclaimed cells → defender spawns (override anything written above in that slot)
    for (r, c) in reclaimed_cells:
        final.board[r][c] = random.randrange(NUM_COLORS)
        if (r, c) not in def_spawn_cells:
            def_spawn_cells.append((r, c))

    return CombatPlan(
        attacker_is_top=attacker_is_top,
        attack_formations=atk_fmts_sorted,
        defense_formations=def_fmts,
        defense_map=def_map,
        fmt_anim=fmt_anim_list,
        hit_def_ids=hit_def_ids,
        breakthrough_cols=breakthrough_cols,
        final_board=final,
        non_fmt_atk_cells=non_fmt_atk,
        atk_slide_map=atk_slide_map,
        def_slide_map=def_slide_map,
        atk_spawn_cells=atk_spawn_cells,
        def_spawn_cells=def_spawn_cells,
    )


# ---------------------------------------------------------------------------
# Animation state machine
# ---------------------------------------------------------------------------

class Phase(Enum):
    # 1+2: non-fmt attackers fade out; defense outlines fade in
    FADE_SETUP    = auto()
    # 3: process formation slides sequentially
    FMT_SLIDE     = auto()
    # 4+5: hit formations disappear; unhit outlines fade; non-fmt fade back
    CLEANUP       = auto()
    # 6: breakthrough reclaim frontier sweep
    RECLAIM       = auto()
    # 7: gap-fill slides (atk + def simultaneously)
    SLIDE_FILL    = auto()
    # 8: new units fade in
    SPAWN         = auto()
    DONE          = auto()

# Durations (seconds)
DUR_FADE_SETUP  = 0.9
DUR_FMT_SPEED   = CELL / 0.35   # pixels per second for formation sliding
DUR_CLEANUP     = 0.6
DUR_RECLAIM     = 0.7
DUR_SLIDE_FILL  = 0.65
DUR_SPAWN       = 0.8


class AnimState:
    def __init__(self, plan: CombatPlan, orig_board: BoardState):
        self.plan  = plan
        self.orig  = orig_board   # immutable reference; never modified
        self.phase = Phase.FADE_SETUP
        self.t     = 0.0    # time within current phase

        # Cells that have been visually killed (not drawn)
        self.dead: set = set()
        # Cumulative elapsed time — used to timestamp deaths for burst animation
        self.total_t: float = 0.0
        # (r,c) -> total_t when the cell was killed, for death-burst duration
        self.dying: dict = {}
        # Frontier as currently displayed
        self.vis_frontier: list = orig_board.frontier[:]
        # Per-column kills so far (used to keep vis_frontier in sync)
        self._col_kills_done = [0] * GRID_COLS

        # Phase 1/5 alpha for non-fmt attackers (255=visible, 0=gone)
        self.non_fmt_alpha: float = 255.0
        # Defense outline alpha
        self.def_outline_alpha: float = 0.0
        # Which defense formation outlines are red (hit confirmed)
        self.red_def_ids: set = set()

        # Phase 3: current formation being animated
        self.cur_fmt_idx: int = 0
        self.cur_slide_px: float = 0.0

        # Phase 7
        self.slide_progress: float = 0.0
        # Phase 8
        self.spawn_alpha: float = 0.0
        # Red flash for breakthrough
        self.flash_alpha: float = 0.0
        # Snapshot of vis_frontier at reclaim start (set in _end_cleanup)
        self._reclaim_start_frontier: list = []

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def kill_cell(self, r, c, death_cy=None):
        """Add (r,c) to dead set and timestamp it for the burst animation.

        death_cy: screen-pixel y of the cell's centre at the moment it dies.
        Defaults to the cell's stationary grid position.
        """
        self.dead.add((r, c))
        if (r, c) not in self.dying:
            if death_cy is None:
                death_cy = r * CELL + GRID_Y + CELL // 2
            self.dying[(r, c)] = (self.total_t, death_cy)

    def _advance_frontier(self, c, kills_delta):
        """Update vis_frontier[c] by kills_delta kills toward enemy."""
        plan = self.plan
        if plan.attacker_is_top:
            self.vis_frontier[c] += kills_delta
        else:
            self.vis_frontier[c] -= kills_delta

    def _trigger_fmt_events(self, fai: FmtAnimInfo, old_px: float, new_px: float):
        """Apply events that occur in the interval (old_px, new_px]."""
        c   = fai.col
        d   = 1 if fai.attacker_is_top else -1   # slide direction in screen y

        def _slid_cy(r, slide_px):
            """Screen-pixel y-centre of formation soldier at row r with slide offset."""
            return int(r * CELL + GRID_Y + int(slide_px * d) + CELL // 2)

        # Frontier cross — front 2 soldiers vanish at the frontier line
        if old_px < fai.frontier_cross_px <= new_px:
            for r in fai.initial_rows[:2]:
                self.kill_cell(r, c, _slid_cy(r, fai.frontier_cross_px))

        # Individual kills — one attacker + one stationary defender disappear
        for k, (px, def_row) in enumerate(zip(fai.kill_px, fai.killed_def_rows)):
            if old_px < px <= new_px:
                soldier_idx = 2 + k
                if soldier_idx < len(fai.initial_rows):
                    atk_r = fai.initial_rows[soldier_idx]
                    self.kill_cell(atk_r, c, _slid_cy(atk_r, px))
                # Defender was stationary — default position is correct
                self.kill_cell(def_row, c)
                self._advance_frontier(c, 1)

        # Block — remaining formation soldiers vanish at the defense wall
        if fai.blocked_by is not None:
            if old_px < fai.end_px <= new_px:
                for r in fai.initial_rows:
                    self.kill_cell(r, c, _slid_cy(r, fai.end_px))
                self.red_def_ids.add(fai.blocked_by)

        # Breakthrough — remaining soldiers slide off the back edge
        if fai.breakthrough:
            if old_px < fai.end_px <= new_px:
                for r in fai.initial_rows:
                    self.kill_cell(r, c, _slid_cy(r, fai.end_px))
                self.flash_alpha = 220.0


# ---------------------------------------------------------------------------
# Animation update
# ---------------------------------------------------------------------------

def advance_anim(state: AnimState, dt: float) -> bool:
    """Step animation by dt seconds. Returns True when complete."""
    plan = state.plan
    state.total_t += dt

    if state.flash_alpha > 0:
        state.flash_alpha = max(0.0, state.flash_alpha - dt * 280)

    if state.phase == Phase.FADE_SETUP:
        state.t += dt
        p = min(state.t / DUR_FADE_SETUP, 1.0)
        state.non_fmt_alpha   = 255.0 * (1.0 - p)
        state.def_outline_alpha = 255.0 * p
        if p >= 1.0:
            state.non_fmt_alpha = 0.0
            state.def_outline_alpha = 255.0
            _start_fmt_slide(state)

    elif state.phase == Phase.FMT_SLIDE:
        _update_fmt_slide(state, dt)

    elif state.phase == Phase.CLEANUP:
        state.t += dt
        p = min(state.t / DUR_CLEANUP, 1.0)
        state.def_outline_alpha = 255.0 * (1.0 - p)
        state.non_fmt_alpha     = 255.0 * p
        if p >= 1.0:
            state.def_outline_alpha = 0.0
            state.non_fmt_alpha     = 255.0
            _end_cleanup(state)

    elif state.phase == Phase.RECLAIM:
        state.t += dt
        p = min(state.t / DUR_RECLAIM, 1.0)
        # Linear interpolation from snapshot taken in _end_cleanup to final frontier
        for c in plan.breakthrough_cols:
            start_f = state._reclaim_start_frontier[c]
            final_f = plan.final_board.frontier[c]
            state.vis_frontier[c] = int(round(start_f + (final_f - start_f) * p))
        if p >= 1.0:
            state.vis_frontier = plan.final_board.frontier[:]
            _start_slide_fill(state)

    elif state.phase == Phase.SLIDE_FILL:
        state.t += dt
        state.slide_progress = min(state.t / DUR_SLIDE_FILL, 1.0)
        if state.slide_progress >= 1.0:
            _start_spawn(state)

    elif state.phase == Phase.SPAWN:
        state.t += dt
        state.spawn_alpha = min(state.t / DUR_SPAWN, 1.0) * 255.0
        if state.t >= DUR_SPAWN:
            state.phase = Phase.DONE

    return state.phase == Phase.DONE


def _start_fmt_slide(state: AnimState):
    state.phase = Phase.FMT_SLIDE
    state.t = 0.0
    state.cur_fmt_idx = 0
    state.cur_slide_px = 0.0
    if not state.plan.attack_formations:
        _start_cleanup(state)


def _update_fmt_slide(state: AnimState, dt: float):
    plan = state.plan
    if state.cur_fmt_idx >= len(plan.attack_formations):
        _start_cleanup(state)
        return

    fai = plan.fmt_anim[state.cur_fmt_idx]
    old_px  = state.cur_slide_px
    new_px  = old_px + dt * DUR_FMT_SPEED
    state._trigger_fmt_events(fai, old_px, new_px)
    state.cur_slide_px = new_px

    if new_px >= fai.end_px:
        state.cur_fmt_idx += 1
        state.cur_slide_px = 0.0
        if state.cur_fmt_idx >= len(plan.attack_formations):
            _start_cleanup(state)


def _start_cleanup(state: AnimState):
    state.phase = Phase.CLEANUP
    state.t = 0.0
    for did in state.plan.hit_def_ids:
        df = state.plan.defense_map[did]
        for (r, c) in df.cells:
            state.kill_cell(r, c)


def _end_cleanup(state: AnimState):
    if state.plan.breakthrough_cols:
        state.phase = Phase.RECLAIM
        state.t = 0.0
        # Snapshot frontier at reclaim start so we can do a clean linear interpolation
        state._reclaim_start_frontier = state.vis_frontier[:]
    else:
        _start_slide_fill(state)


def _start_slide_fill(state: AnimState):
    state.phase = Phase.SLIDE_FILL
    state.t = 0.0
    state.slide_progress = 0.0
    state.vis_frontier = state.plan.final_board.frontier[:]


def _start_spawn(state: AnimState):
    state.phase = Phase.SPAWN
    state.t = 0.0
    state.spawn_alpha = 0.0


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

DEATH_DUR = 0.38   # seconds for the particle-burst death animation


def draw_death_burst(surf, color_idx, cx, cy, age):
    """5 coloured dots scatter from (cx, cy) — the cell's position at death."""
    t = min(age / DEATH_DUR, 1.0)
    alpha = int(255 * max(0.0, 1.0 - t) ** 0.55)
    if alpha <= 0:
        return
    color   = UNIT_RGB[color_idx]
    speed   = CELL * 1.1
    gravity = CELL * 3.2
    for i in range(5):
        ang = math.radians(i * 72 + 18)
        px  = int(cx + math.cos(ang) * speed * age)
        py  = int(cy + math.sin(ang) * speed * age + 0.5 * gravity * age * age)
        rp  = max(1, int(CELL * 0.10 * (1.0 - t * 0.55)))
        s   = pygame.Surface((rp * 2 + 2, rp * 2 + 2), pygame.SRCALPHA)
        pygame.draw.circle(s, (*color, alpha), (rp + 1, rp + 1), rp)
        surf.blit(s, (px - rp - 1, py - rp - 1))


def draw_bg(surf, frontier):
    for r in range(GRID_ROWS):
        for c in range(GRID_COLS):
            color = BG_TOP if r <= frontier[c] else BG_BOTTOM
            pygame.draw.rect(surf, color, cell_rect(r, c))


def draw_frontier_dots(surf, frontier):
    for c in range(GRID_COLS):
        f = frontier[c]
        if f < 0 or f >= GRID_ROWS - 1:
            continue
        y  = (f + 1) * CELL + GRID_Y
        x0 = c * CELL + 5
        x1 = (c + 1) * CELL - 5
        x = x0
        while x < x1:
            pygame.draw.line(surf, C_FRONTIER, (x, y), (min(x+5, x1), y), 2)
            x += 9


def draw_grid(surf):
    for r in range(GRID_ROWS + 1):
        y = r * CELL + GRID_Y
        pygame.draw.line(surf, C_GRID, (0, y), (GRID_COLS * CELL, y), 1)
    for c in range(GRID_COLS + 1):
        x = c * CELL
        pygame.draw.line(surf, C_GRID, (x, GRID_Y), (x, GRID_ROWS * CELL + GRID_Y), 1)


def draw_static(surf, bs: BoardState, active_top=None, anim_t=0.0):
    """Draw the board.  active_top: True/False animates only that side; None animates all."""
    draw_bg(surf, bs.frontier)
    for r in range(GRID_ROWS):
        for c in range(GRID_COLS):
            ci = bs.board[r][c]
            is_top = bs.owner_is_top(r, c)
            t = anim_t if (active_top is None or is_top == active_top) else 0.0
            draw_unit(surf, ci, cell_rect(r, c).inflate(-10, -10),
                      filled=not is_top, anim_t=t)
    draw_frontier_dots(surf, bs.frontier)
    draw_grid(surf)


def _unit_rect_at(pixel_y, c):
    return pygame.Rect(c * CELL, pixel_y, CELL, CELL).inflate(-10, -10)


def draw_animated(surf, state: AnimState, anim_t=0.0):
    plan  = state.plan
    orig  = state.orig
    final = plan.final_board
    phase = state.phase

    # Local shortcut that bakes in the current anim_t for all unit draws.
    def du(dst, ci, rect, filled, alpha=255):
        draw_unit(dst, ci, rect, filled, alpha, anim_t)

    draw_bg(surf, state.vis_frontier)

    # Build lookup sets for fast decisions
    non_fmt_set = set(plan.non_fmt_atk_cells)
    all_fmt_cells: dict = {}   # (r,c) → fmt_idx
    for i, af in enumerate(plan.attack_formations):
        for r in af.rows:
            all_fmt_cells[(r, af.col)] = i

    def_cell_map: dict = {}    # (r,c) → defense formation id
    for df in plan.defense_formations:
        for cell in df.cells:
            def_cell_map[cell] = df.formation_id

    atk_top = plan.attacker_is_top
    direction = 1 if atk_top else -1

    for r in range(GRID_ROWS):
        for c in range(GRID_COLS):
            cell = (r, c)
            ci   = orig.board[r][c]
            filled = not orig.owner_is_top(r, c)

            # ── Dead cells — play burst at where the unit actually died ─
            if cell in state.dead:
                if cell in state.dying:
                    death_time, death_cy = state.dying[cell]
                    age = state.total_t - death_time
                    if age < DEATH_DUR:
                        death_cx = c * CELL + CELL // 2
                        draw_death_burst(surf, ci, death_cx, death_cy, age)
                continue

            # ── Non-formation attackers (fade out phase 1, fade in phase 5)
            if cell in non_fmt_set:
                alpha = int(state.non_fmt_alpha)
                if alpha > 0:
                    if phase in (Phase.SLIDE_FILL, Phase.SPAWN, Phase.DONE):
                        if cell in plan.atk_slide_map:
                            nr, nc = plan.atk_slide_map[cell]
                            tp = state.slide_progress
                            py = int((r + (nr - r) * tp) * CELL + GRID_Y)
                            du(surf, ci, _unit_rect_at(py, c), filled, 255)
                        else:
                            du(surf, ci, cell_rect(r, c).inflate(-10,-10), filled, 255)
                    else:
                        du(surf, ci, cell_rect(r,c).inflate(-10,-10), filled, alpha)
                continue

            # ── Attack formation cells ───────────────────────────────────
            if cell in all_fmt_cells:
                fi = all_fmt_cells[cell]
                if fi < state.cur_fmt_idx:
                    continue
                elif fi == state.cur_fmt_idx and phase == Phase.FMT_SLIDE:
                    fai = plan.fmt_anim[fi]
                    n   = fai.soldiers_at(state.cur_slide_px)
                    active = fai.initial_rows[-n:] if n > 0 else []
                    if r in active:
                        py = int(r * CELL + GRID_Y + state.cur_slide_px * direction)
                        du(surf, ci, _unit_rect_at(py, c), filled, 255)
                else:
                    du(surf, ci, cell_rect(r,c).inflate(-10,-10), filled, 255)
                continue

            # ── Defense formation cells ──────────────────────────────────
            if cell in def_cell_map:
                if cell in plan.def_slide_map and phase in (Phase.SLIDE_FILL, Phase.SPAWN, Phase.DONE):
                    nr, nc = plan.def_slide_map[cell]
                    if phase == Phase.SLIDE_FILL:
                        tp = state.slide_progress
                        py = int((r + (nr - r) * tp) * CELL + GRID_Y)
                        du(surf, ci, _unit_rect_at(py, c), filled, 255)
                    else:
                        du(surf, ci, cell_rect(nr,nc).inflate(-10,-10), filled, 255)
                else:
                    du(surf, ci, cell_rect(r,c).inflate(-10,-10), filled, 255)
                continue

            # ── Attacker slide-fill cells ────────────────────────────────
            if cell in plan.atk_slide_map:
                nr, nc = plan.atk_slide_map[cell]
                if phase == Phase.SLIDE_FILL:
                    tp = state.slide_progress
                    py = int((r + (nr - r) * tp) * CELL + GRID_Y)
                    du(surf, ci, _unit_rect_at(py, c), filled, 255)
                elif phase in (Phase.SPAWN, Phase.DONE):
                    du(surf, ci, cell_rect(nr,nc).inflate(-10,-10), filled, 255)
                else:
                    du(surf, ci, cell_rect(r,c).inflate(-10,-10), filled, 255)
                continue

            # ── Defender slide-fill cells ────────────────────────────────
            if cell in plan.def_slide_map:
                nr, nc = plan.def_slide_map[cell]
                if phase == Phase.SLIDE_FILL:
                    tp = state.slide_progress
                    py = int((r + (nr - r) * tp) * CELL + GRID_Y)
                    du(surf, ci, _unit_rect_at(py, c), not final.owner_is_top(r, c), 255)
                elif phase in (Phase.SPAWN, Phase.DONE):
                    du(surf, ci, cell_rect(nr,nc).inflate(-10,-10),
                       not final.owner_is_top(nr, nc), 255)
                else:
                    du(surf, ci, cell_rect(r,c).inflate(-10,-10), filled, 255)
                continue

            # ── Normal cell ──────────────────────────────────────────────
            du(surf, ci, cell_rect(r,c).inflate(-10,-10), filled, 255)

    # Spawn cells (phase 8) — taken from final_board
    if phase in (Phase.SPAWN, Phase.DONE):
        alpha = int(state.spawn_alpha)
        for (r, c) in plan.atk_spawn_cells:
            ci = final.board[r][c]
            du(surf, ci, cell_rect(r,c).inflate(-10,-10),
               not final.owner_is_top(r, c), alpha)
        for (r, c) in plan.def_spawn_cells:
            ci = final.board[r][c]
            du(surf, ci, cell_rect(r,c).inflate(-10,-10),
               not final.owner_is_top(r, c), alpha)

    # Defense formation outlines
    alpha = int(state.def_outline_alpha)
    if alpha > 0:
        for df in plan.defense_formations:
            if df.formation_id in state.red_def_ids or \
               df.formation_id in plan.hit_def_ids:
                oc = (*C_DEF_HIT, alpha)
            else:
                oc = (*C_DEF_NORMAL, alpha)
            for (r, c) in df.cells:
                if (r, c) in state.dead:
                    continue
                s = pygame.Surface((CELL, CELL), pygame.SRCALPHA)
                pygame.draw.rect(s, oc, (1, 1, CELL-2, CELL-2), 3)
                surf.blit(s, cell_rect(r, c).topleft)

    draw_frontier_dots(surf, state.vis_frontier)
    draw_grid(surf)

    # Red flash overlay for breakthrough
    if state.flash_alpha > 0:
        ov = pygame.Surface((GRID_COLS * CELL, GRID_ROWS * CELL), pygame.SRCALPHA)
        ov.fill((255, 0, 0, int(min(state.flash_alpha, 160))))
        surf.blit(ov, (0, GRID_Y))


# ---------------------------------------------------------------------------
# HUD / Sidebar
# ---------------------------------------------------------------------------

def draw_hud(surf, fonts, atk_is_top, turn):
    """Top bar: title + turn indicator only. HP lives on the player bars."""
    font, bfont, _ = fonts
    pygame.draw.rect(surf, BG_HUD, (0, 0, SCREEN_W, HUD_H))
    title = bfont.render("MAGE GRID", True, (215, 192, 120))
    surf.blit(title, (8, 6))
    who = f"Turn {turn}  ·  {'TOP' if atk_is_top else 'BOTTOM'} attacks"
    surf.blit(font.render(who, True, (160, 160, 160)), (8, 30))


def draw_player_bars(surf, fonts, top_name, bot_name, hp_top, hp_bottom):
    """Name + HP bar strip directly above and below the grid."""
    font, bfont, _ = fonts
    grid_w = GRID_COLS * CELL

    # ── Top player bar ──────────────────────────────────────────────────
    ty = HUD_H
    pygame.draw.rect(surf, (42, 26, 10), (0, ty, grid_w, PLAYER_BAR_H))
    name_s = bfont.render(top_name, True, (210, 185, 140))
    surf.blit(name_s, (8, ty + (PLAYER_BAR_H - name_s.get_height()) // 2))
    _player_hp_bar(surf, font, hp_top,
                   grid_w - 202, ty + (PLAYER_BAR_H - 14) // 2,
                   160, (90, 145, 255))

    # ── Bottom player bar ────────────────────────────────────────────────
    by_ = GRID_Y + GRID_ROWS * CELL
    pygame.draw.rect(surf, (115, 82, 40), (0, by_, grid_w, PLAYER_BAR_H))
    name_s = bfont.render(bot_name, True, (255, 240, 195))
    surf.blit(name_s, (8, by_ + (PLAYER_BAR_H - name_s.get_height()) // 2))
    _player_hp_bar(surf, font, hp_bottom,
                   grid_w - 202, by_ + (PLAYER_BAR_H - 14) // 2,
                   160, (255, 100, 45))


def _player_hp_bar(surf, font, hp, x, y, bw, color):
    bh = 14
    pygame.draw.rect(surf, (40, 40, 40), (x, y, bw, bh))
    fw = int(bw * max(0, hp) / HP_MAX)
    pygame.draw.rect(surf, color, (x, y, fw, bh))
    pygame.draw.rect(surf, (90, 90, 90), (x, y, bw, bh), 1)
    hp_s = font.render(str(hp), True, (220, 220, 220))
    surf.blit(hp_s, (x + bw + 6, y + 1))


def draw_sidebar(surf, fonts, sx, gs, pick_info=None):
    """
    pick_info (only during color-select phase):
        {"who": str, "role": str, "role_color": tuple,
         "confirm_msg": str, "confirm_timer": float}
    """
    font, bfont, _ = fonts
    pygame.draw.rect(surf, BG_SIDEBAR, (sx, 0, SIDEBAR_W, SCREEN_H))

    y = HUD_H + 10

    # ── Who is picking (pick phase only) ────────────────────────────────
    if pick_info:
        lbl = bfont.render(
            f"{pick_info['who']}  —  {pick_info['role']}",
            True, pick_info["role_color"])
        surf.blit(lbl, (sx + 8, y)); y += lbl.get_height() + 4
        inst = font.render("Pick 2 colors, then Enter", True, (140, 140, 135))
        surf.blit(inst, (sx + 8, y)); y += inst.get_height() + 4

        if pick_info.get("confirm_msg") and pick_info.get("confirm_timer", 0) > 0:
            cm = font.render(pick_info["confirm_msg"], True, (150, 240, 125))
            surf.blit(cm, (sx + 8, y))
        y += 20  # fixed gap whether or not confirm is shown

        pygame.draw.line(surf, (55, 50, 44),
                         (sx + 8, y), (sx + SIDEBAR_W - 8, y), 1)
        y += 10

    # ── Color legend ────────────────────────────────────────────────────
    surf.blit(bfont.render("Colors", True, (200, 178, 110)), (sx + 8, y)); y += 22
    for i in range(NUM_COLORS):
        pygame.draw.circle(surf, UNIT_RGB[i], (sx + 20, y + 8), 7)
        surf.blit(font.render(f"[{COLOR_KEYS[i]}] {COLOR_NAMES[i]}",
                              True, (195, 195, 195)), (sx + 32, y))
        y += 20
    y += 10

    # ── Confirmed selections (pick phase only) ───────────────────────────
    if "atk_colors" in gs:
        surf.blit(font.render("Attack:", True, (255, 155, 70)), (sx + 8, y))
        for i, ci in enumerate(gs["atk_colors"]):
            pygame.draw.circle(surf, UNIT_RGB[ci], (sx + 78 + i * 24, y + 8), 8)
        y += 24
    if "def_colors" in gs:
        surf.blit(font.render("Defend:", True, (70, 185, 255)), (sx + 8, y))
        for i, ci in enumerate(gs["def_colors"]):
            pygame.draw.circle(surf, UNIT_RGB[ci], (sx + 78 + i * 24, y + 8), 8)
        y += 24

    # ── Footer ──────────────────────────────────────────────────────────
    fy = SCREEN_H - 72
    pygame.draw.line(surf, (55, 50, 44),
                     (sx + 8, fy), (sx + SIDEBAR_W - 8, fy), 1)
    fy += 8
    for line in ["B R Y G W = colors", "Enter to confirm", "Click to select"]:
        surf.blit(font.render(line, True, (105, 103, 96)), (sx + 8, fy))
        fy += 18


# ---------------------------------------------------------------------------
# Color picker widget
# ---------------------------------------------------------------------------

class ColorPicker:
    SZ = 38

    def __init__(self, sx):
        self.sx  = sx
        self.sel = []
        # Sits below the color legend in the sidebar.
        # legend starts at ~HUD_H+92 (with pick_info header above), 5×20px = 100px → ~HUD_H+214
        y0 = HUD_H + 222
        self.btns = [(pygame.Rect(sx+8+i*(self.SZ+6), y0, self.SZ, self.SZ), i)
                     for i in range(NUM_COLORS)]

    def reset(self):  self.sel = []
    def ready(self):  return len(self.sel) == 2
    def get(self):    return tuple(self.sel)

    def handle_click(self, pos):
        for rect, ci in self.btns:
            if rect.collidepoint(pos):
                if ci in self.sel: self.sel.remove(ci)
                elif len(self.sel) < 2: self.sel.append(ci)
                return True
        return False

    def handle_key(self, key):
        km = {pygame.K_b:0, pygame.K_r:1, pygame.K_y:2, pygame.K_g:3, pygame.K_w:4}
        ci = km.get(key)
        if ci is not None:
            if ci in self.sel: self.sel.remove(ci)
            elif len(self.sel) < 2: self.sel.append(ci)

    def draw(self, surf, font):
        for rect, ci in self.btns:
            pygame.draw.rect(surf, UNIT_RGB[ci], rect, border_radius=7)
            bw = 3 if ci in self.sel else 1
            bc = (255,255,255) if ci in self.sel else (70,70,70)
            pygame.draw.rect(surf, bc, rect, bw, border_radius=7)
            surf.blit(font.render(COLOR_KEYS[ci], True, (0,0,0)),
                      (rect.x+13, rect.y+11))
        y = self.btns[0][0].bottom + 8
        if self.sel:
            txt = " + ".join(COLOR_NAMES[ci] for ci in self.sel)
        else:
            txt = "Pick 2 colors"
        surf.blit(font.render(txt, True, (195,195,175)), (self.sx+8, y))


# ---------------------------------------------------------------------------
# Text input
# ---------------------------------------------------------------------------

class TextInput:
    def __init__(self, prompt, maxlen=18):
        self.prompt = prompt
        self.text   = ""
        self.maxlen = maxlen
        self.blink  = 0.0

    def update(self, dt): self.blink = (self.blink + dt) % 1.0

    def handle(self, ev):
        if ev.key == pygame.K_BACKSPACE:
            self.text = self.text[:-1]
        elif ev.key == pygame.K_RETURN:
            return True
        elif len(self.text) < self.maxlen and ev.unicode.isprintable():
            self.text += ev.unicode
        return False

    def value(self): return self.text.strip() or "Player"

    def draw(self, surf, font, bfont, x, y, active=True):
        alpha = 255 if active else 110
        s = pygame.Surface((340, 58), pygame.SRCALPHA)
        lbl = bfont.render(self.prompt, True, (195,195,195,alpha))
        s.blit(lbl, (0, 0))
        cur = "|" if (active and self.blink < 0.5) else " "
        txt = font.render(self.text + cur, True, (255,238,170,alpha))
        s.blit(txt, (0, 28))
        surf.blit(s, (x, y))


# ---------------------------------------------------------------------------
# Title screen
# ---------------------------------------------------------------------------

def draw_title(surf, fonts, t):
    _, bfont, tfont = fonts
    surf.fill((12, 8, 4))
    # Animated stars
    for i in range(40):
        random.seed(i * 131 + 7)
        x = random.randint(0, SCREEN_W)
        y = random.randint(0, SCREEN_H)
        a = int(100 + 100 * math.sin(t*1.3 + i*0.7))
        s = pygame.Surface((3,3), pygame.SRCALPHA)
        pygame.draw.circle(s, (210,195,140,a), (1,1), 1)
        surf.blit(s, (x,y))
    title = tfont.render("MAGE GRID", True, (218, 192, 100))
    surf.blit(title, (SCREEN_W//2 - title.get_width()//2, SCREEN_H//3 - 50))
    sub = bfont.render("A tactical grid battle", True, (150,140,120))
    surf.blit(sub, (SCREEN_W//2 - sub.get_width()//2, SCREEN_H//3 + 38))
    pulse = int(190 + 60 * math.sin(t * 2.6))
    go = bfont.render("Press ENTER to begin", True, (pulse, pulse, pulse))
    surf.blit(go, (SCREEN_W//2 - go.get_width()//2, SCREEN_H//2 + 60))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    pygame.init()
    screen = pygame.display.set_mode((SCREEN_W, SCREEN_H))
    pygame.display.set_caption("Mage Grid")
    clock = pygame.time.Clock()

    font   = pygame.font.SysFont("segoeui",    16)
    bfont  = pygame.font.SysFont("segoeui",    18, bold=True)
    tfont  = pygame.font.SysFont("segoeuiblack", 50, bold=True)
    fonts  = (font, bfont, tfont)
    sx     = GRID_COLS * CELL

    phase        = "title"
    t            = 0.0
    top_name     = "Top"
    bot_name     = "Bottom"
    bs: BoardState = make_board()
    turn         = 1
    atk_is_top   = True
    anim: Optional[AnimState] = None

    # Name entry
    name_step = 0
    inputs    = [TextInput("Top player name:"), TextInput("Bottom player name:")]

    picker     = ColorPicker(sx)
    color_step = 0     # 0=attacker picks, 1=defender picks
    atk_colors = None
    def_colors = None
    confirm_msg   = ""
    confirm_timer = 0.0
    gs            = {"phase": "title"}

    while True:
        dt = clock.tick(FPS) / 1000.0
        t += dt
        confirm_timer = max(0.0, confirm_timer - dt)

        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                pygame.quit(); sys.exit()

            if phase == "title":
                if ev.type == pygame.KEYDOWN and ev.key == pygame.K_RETURN:
                    phase = "name_entry"
                    name_step = 0
                    inputs[0].text = ""; inputs[1].text = ""

            elif phase == "name_entry":
                if ev.type == pygame.KEYDOWN:
                    if inputs[name_step].handle(ev):
                        name_step += 1
                        if name_step >= 2:
                            top_name = inputs[0].value()
                            bot_name = inputs[1].value()
                            bs = make_board()
                            phase = "pick"; color_step = 0; picker.reset()
                            gs = {"phase": "pick"}

            elif phase == "pick":
                if ev.type == pygame.KEYDOWN:
                    if ev.key == pygame.K_RETURN and picker.ready():
                        if color_step == 0:
                            atk_colors = picker.get()
                            color_step = 1; picker.reset()
                            confirm_msg = (f"Attack: "
                                f"{COLOR_NAMES[atk_colors[0]]} + "
                                f"{COLOR_NAMES[atk_colors[1]]}")
                            confirm_timer = 1.8
                        else:
                            def_colors = picker.get()
                            plan = build_combat_plan(bs, atk_is_top,
                                                     atk_colors, def_colors)
                            anim  = AnimState(plan, bs)
                            phase = "anim"
                            gs = {"phase": "anim",
                                  "atk_colors": atk_colors,
                                  "def_colors": def_colors}
                    else:
                        picker.handle_key(ev.key)
                if ev.type == pygame.MOUSEBUTTONDOWN:
                    picker.handle_click(ev.pos)

            elif phase == "gameover":
                if ev.type == pygame.KEYDOWN and ev.key == pygame.K_RETURN:
                    phase = "title"

        # Update
        if phase == "name_entry":
            inputs[name_step].update(dt)

        if phase == "anim" and anim is not None:
            if advance_anim(anim, dt):
                bs         = anim.plan.final_board
                turn      += 1
                atk_is_top = not atk_is_top
                anim       = None
                if bs.hp_top <= 0 or bs.hp_bottom <= 0:
                    phase = "gameover"; gs = {"phase": "gameover"}
                else:
                    phase = "pick"; color_step = 0; picker.reset()
                    atk_colors = None; def_colors = None
                    gs = {"phase": "pick"}

        # Draw
        screen.fill((10, 8, 5))

        if phase == "title":
            draw_title(screen, fonts, t)

        elif phase == "name_entry":
            screen.fill((18, 14, 10))
            hdr = bfont.render("Enter Player Names", True, (200, 178, 110))
            screen.blit(hdr, (SCREEN_W//2 - hdr.get_width()//2, 70))
            for i, inp in enumerate(inputs):
                inp.draw(screen, font, bfont,
                         SCREEN_W//2 - 170, 150 + i*90,
                         active=(i == name_step))

        elif phase == "pick":
            # Animate whichever side is currently picking colors.
            picking_top = atk_is_top if color_step == 0 else (not atk_is_top)
            draw_static(screen, bs, active_top=picking_top, anim_t=t)
            draw_hud(screen, fonts, atk_is_top, turn)
            draw_player_bars(screen, fonts, top_name, bot_name,
                             bs.hp_top, bs.hp_bottom)
            atker = top_name if atk_is_top else bot_name
            defer = bot_name  if atk_is_top else top_name
            who   = atker if color_step == 0 else defer
            role  = "ATTACKING" if color_step == 0 else "DEFENDING"
            rc    = (255, 155, 55) if color_step == 0 else (65, 175, 255)
            pick_info = {
                "who": who, "role": role, "role_color": rc,
                "confirm_msg": confirm_msg, "confirm_timer": confirm_timer,
            }
            draw_sidebar(screen, fonts, sx, gs, pick_info)
            picker.draw(screen, font)

        elif phase == "anim" and anim is not None:
            # Animate all units during the combat sequence.
            draw_animated(screen, anim, anim_t=t)
            draw_hud(screen, fonts, atk_is_top, turn)
            draw_player_bars(screen, fonts, top_name, bot_name,
                             bs.hp_top, bs.hp_bottom)
            draw_sidebar(screen, fonts, sx, gs)

        elif phase == "gameover":
            draw_static(screen, bs)
            draw_hud(screen, fonts, atk_is_top, turn)
            draw_player_bars(screen, fonts, top_name, bot_name,
                             bs.hp_top, bs.hp_bottom)
            ov = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)
            ov.fill((0,0,0,155)); screen.blit(ov, (0,0))
            cx = SCREEN_W // 2
            if bs.hp_top <= 0 and bs.hp_bottom <= 0:
                msg = "DRAW!"
            elif bs.hp_top <= 0:
                msg = f"{bot_name} WINS!"
            else:
                msg = f"{top_name} WINS!"
            wl = tfont.render(msg, True, (255, 218, 70))
            screen.blit(wl, (cx - wl.get_width()//2, SCREEN_H//3))
            sub = font.render("Press ENTER to return to title", True, (175,175,175))
            screen.blit(sub, (cx - sub.get_width()//2, SCREEN_H//2 + 20))

        pygame.display.flip()


if __name__ == "__main__":
    main()
