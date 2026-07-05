"""
Mage Grid — Sprite Edition.

Same rules and combat engine as mage_grid.py, with pixel-art mage sprites:

  * Units are element mages (water / fire / lightning / nature / earth),
    white robes on the left army, black robes on the right.
  * While a player is picking colors, their army idles with a 3-frame
    bob / robe-swish loop plus ambient element effects.
  * When an attack launches, each formation flashes, collapses and
    transforms into a ball of its element, then marches across the field.
  * Defense formations cast: staff flare, then an element force-field wall
    rises along the formation's front edge — one segment per mage, so
    two-color formations raise a striped wall.
  * The first time an attack ball breaks against a field, the wall turns
    weakened (dimmed, holed, fractured) and the spent mages bow over,
    breathing hard, until the formation dissolves.

Sprites are loaded from assets/sprites.png (regenerate with gen_sprites.py).

Run:  python3 mage_grid.py
"""

import itertools
import json
import os
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

GRID_COLS = 10
GRID_ROWS = 9
STARTING_FRONTIER = 4          # cols 0-4 left, 5-9 right

CELL = 62
SIDEBAR_W = 230
HUD_H = 72
PLAYER_BAR_H = 28               # strip above grid, split left/right
GRID_Y = HUD_H + PLAYER_BAR_H  # pixel y where the grid starts
SCREEN_W = GRID_COLS * CELL + SIDEBAR_W
SCREEN_H = GRID_Y + GRID_ROWS * CELL
FPS = 60

# ---------------------------------------------------------------------------
# Color / unit definitions
# ---------------------------------------------------------------------------

ELEM_NAMES = ["Water", "Fire", "Lightning", "Nature", "Rock"]
ELEM_KEYS  = ["W",     "F",    "L",         "N",      "R"]
NUM_COLORS = 5
HP_MAX     = 5

UNIT_RGB = {
    0: (90,  150, 255),   # Blue   — water
    1: (255,  60,  45),   # Red    — fire
    2: (255, 225,  30),   # Yellow — lightning
    3: ( 50, 195,  75),   # Green  — nature
    4: (215, 215, 215),   # White  — earth
}

BG_LEFT      = (55,  32,  12)
BG_RIGHT     = (172, 133,  78)
BG_SIDEBAR   = (28,  22,  16)
BG_HUD       = (18,  15,  10)
C_GRID       = (0,   0,   0)
C_FRONTIER   = (255, 255, 255)

# ---------------------------------------------------------------------------
# Sprite sheet
# ---------------------------------------------------------------------------
# sheet columns (24x24 tiles, one row per color index):
#   0-2   left idle f0-f2        3-5   right idle f0-f2
#   6-8   left cast c0-c2        9-11  right cast c0-c2
#   12-13 left tired t0-t1       14-15 right tired t0-t1
#   16-18 transform (left-facing; mirrored at load for the right army)
#   19-21 force field (front on the right; mirrored for right defenders)
#   22-24 weakened field (same facing rule)
#   25-28 ball f0-f3 (mirrored for right attackers so rolling reads right)
#   29-30 left scared s0-s1     31-32 right scared s0-s1
#   33-34 left celebrate c0-c1  35-36 right celebrate c0-c1
# ---------------------------------------------------------------------------

SHEET_TILE = 24
SPRITE_PX  = 48                  # 2x integer scale, centered in the cell
SPRITE_OFF = (CELL - SPRITE_PX) // 2

IDLE_SEQ       = [0, 1, 2, 1]    # base, bob, swish, bob
IDLE_FRAME_S   = 0.30
BALL_FRAME_S   = 0.12
CAST_FRAME_S   = 0.18
TIRED_FRAME_S  = 0.35
FIELD_FRAME_S  = 0.16
TF_FRAME_S     = 0.18
TRANSFORM_DUR  = 3 * TF_FRAME_S  # formation transforms before marching

SPRITES = {}                     # SPRITES[color]["idle"]["left"][frame] etc.


def load_sprites():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "assets", "sprites.png")
    sheet = pygame.image.load(path).convert_alpha()

    def tile(col, row, flip=False):
        s = sheet.subsurface((col * SHEET_TILE, row * SHEET_TILE,
                              SHEET_TILE, SHEET_TILE))
        s = pygame.transform.scale(s, (SPRITE_PX, SPRITE_PX))
        if flip:
            s = pygame.transform.flip(s, True, False)
        return s

    for ci in range(NUM_COLORS):
        SPRITES[ci] = {
            "idle":  {"left":  [tile(c, ci) for c in range(0, 3)],
                      "right": [tile(c, ci) for c in range(3, 6)]},
            "cast":  {"left":  [tile(c, ci) for c in range(6, 9)],
                      "right": [tile(c, ci) for c in range(9, 12)]},
            "tired": {"left":  [tile(c, ci) for c in range(12, 14)],
                      "right": [tile(c, ci) for c in range(14, 16)]},
            "tf":    {"left":  [tile(c, ci) for c in range(16, 19)],
                      "right": [tile(c, ci, True) for c in range(16, 19)]},
            "fld":   {"left":  [tile(c, ci) for c in range(19, 22)],
                      "right": [tile(c, ci, True) for c in range(19, 22)]},
            "dmg":   {"left":  [tile(c, ci) for c in range(22, 25)],
                      "right": [tile(c, ci, True) for c in range(22, 25)]},
            "ball":  {"left":  [tile(c, ci) for c in range(25, 29)],
                      "right": [tile(c, ci, True) for c in range(25, 29)]},
            "scared": {"left":  [tile(c, ci) for c in range(29, 31)],
                       "right": [tile(c, ci) for c in range(31, 33)]},
            "celebrate": {"left":  [tile(c, ci) for c in range(33, 35)],
                          "right": [tile(c, ci) for c in range(35, 37)]},
        }

    _build_bg_tiles()


# ---------------------------------------------------------------------------
# Ground texture — each territory color gets a handful of pre-rendered tile
# variants speckled with pebbles in in-between browns; a fixed random map
# assigns a variant to every board cell so the field looks naturally varied
# without repeating and without flickering between frames.
# ---------------------------------------------------------------------------

BG_VARIANTS = 8
BG_TILES = {}          # side -> [Surface] * BG_VARIANTS
BG_MAP = []            # BG_MAP[r][c] -> variant index

# small shaded stones (body, shadow) per side
BG_STONE = {
    "left":  ((82, 52, 26), (38, 22, 8)),
    "right": ((150, 113, 64), (128, 94, 52)),
}


def _shade(base, delta):
    return tuple(max(0, min(255, ch + d)) for ch, d in zip(base, delta))


def _build_bg_tiles():
    """Organic ground: smoothed value-noise quantized to chunky 2x2 blocks
    (the sprites' pixel scale), banded into faintly lighter/darker earth,
    plus the occasional blocky pebble cluster."""
    rng = random.Random(1207)
    B = 2                       # texture pixel size
    G = CELL // B               # blocks per tile side
    C = 4                       # coarse noise grid (bigger = finer blobs)
    for side, base in (("left", BG_LEFT), ("right", BG_RIGHT)):
        lighter = _shade(base, (7, 5, 3))
        darker = _shade(base, (-7, -5, -3))
        tiles = []
        for _ in range(BG_VARIANTS):
            s = pygame.Surface((CELL, CELL))
            s.fill(base)
            grid = [[rng.random() for _ in range(C + 1)]
                    for _ in range(C + 1)]
            for by in range(G):
                for bx in range(G):
                    u, v = bx / G * C, by / G * C
                    i, j = int(u), int(v)
                    fu, fv = u - i, v - j
                    fu = fu * fu * (3 - 2 * fu)     # smoothstep
                    fv = fv * fv * (3 - 2 * fv)
                    val = (grid[j][i] * (1 - fu) * (1 - fv) +
                           grid[j][i + 1] * fu * (1 - fv) +
                           grid[j + 1][i] * (1 - fu) * fv +
                           grid[j + 1][i + 1] * fu * fv)
                    if val > 0.64:
                        col = lighter
                    elif val < 0.36:
                        col = darker
                    else:
                        continue
                    pygame.draw.rect(s, col, (bx * B, by * B, B, B))
            # about a third of tiles get a small cluster of blocky stones
            if rng.random() < 0.35:
                body, shadow = BG_STONE[side]
                cx = rng.randrange(4, G - 5) * B
                cy = rng.randrange(4, G - 5) * B
                for _ in range(rng.randint(2, 3)):
                    px = cx + rng.randrange(-3, 4) * B
                    py = cy + rng.randrange(-3, 4) * B
                    w = rng.choice((4, 6))
                    pygame.draw.rect(s, shadow, (px, py + 2, w, 4))
                    pygame.draw.rect(s, body, (px, py, w, 4))
            tiles.append(s.convert())
        BG_TILES[side] = tiles
    BG_MAP.clear()
    BG_MAP.extend([[rng.randrange(BG_VARIANTS) for _ in range(GRID_COLS)]
                   for _ in range(GRID_ROWS)])


def blit_sprite(dest, surf, x, y, alpha=255):
    if alpha >= 255:
        dest.blit(surf, (x, y))
    elif alpha > 0:
        # scale the per-pixel alpha directly: set_alpha() on SRCALPHA
        # surfaces is renderer-dependent (can draw opaque black boxes on
        # some platforms); BLEND_RGBA_MULT is pure surface math everywhere
        tmp = surf.copy()
        tmp.fill((255, 255, 255, alpha), special_flags=pygame.BLEND_RGBA_MULT)
        dest.blit(tmp, (x, y))


def idle_frame(anim_t):
    return IDLE_SEQ[int(anim_t / IDLE_FRAME_S) % len(IDLE_SEQ)] if anim_t else 0


def draw_mage(dest, color_idx, side, cell_x, cell_y, alpha=255, anim_t=0.0,
              pose="idle", pose_frame=0):
    """Draw a mage sprite whose cell's top-left pixel is (cell_x, cell_y)."""
    if pose == "idle":
        frame = idle_frame(anim_t)
    else:
        frame = pose_frame
    surf = SPRITES[color_idx][pose][side][frame]
    blit_sprite(dest, surf, cell_x + SPRITE_OFF, cell_y + SPRITE_OFF, alpha)


def draw_ball(dest, color_idx, side, cell_x, cell_y, frame, alpha=255):
    surf = SPRITES[color_idx]["ball"][side][frame % 4]
    blit_sprite(dest, surf, cell_x + SPRITE_OFF, cell_y + SPRITE_OFF, alpha)


def draw_field(dest, color_idx, def_side, cell_x, cell_y, frame,
               damaged=False, alpha=255):
    """Field band hugs the formation's front edge (enemy-facing)."""
    bank = "dmg" if damaged else "fld"
    surf = SPRITES[color_idx][bank][def_side][frame % 3]
    # left defender faces right: align tile's right edge with the cell's;
    # right defender (mirrored band) aligns with the cell's left edge.
    x = cell_x + (CELL - SPRITE_PX if def_side == "left" else 0)
    blit_sprite(dest, surf, x, cell_y + SPRITE_OFF, alpha)


def cell_rect(r, c):
    return pygame.Rect(c * CELL, r * CELL + GRID_Y, CELL, CELL)


def cell_xy(r, c):
    return c * CELL, r * CELL + GRID_Y


# ---------------------------------------------------------------------------
# Board state
# ---------------------------------------------------------------------------

@dataclass
class BoardState:
    board: list      # board[r][c] = color index 0-4
    frontier: list   # frontier[r] = last col owned by left player
    hp_left: int = HP_MAX
    hp_right: int = HP_MAX

    def copy(self):
        return BoardState(
            [row[:] for row in self.board],
            self.frontier[:],
            self.hp_left,
            self.hp_right,
        )

    def owner_is_left(self, r, c):
        return c <= self.frontier[r]


# The first attacker has a measurable edge (~56% in AI-vs-AI testing), so
# the first DEFENDER starts one tile deeper in the two rows flanking the
# middle (rows 4 and 6, counting 1-9). Self-play shows this evens the match
# to ~49/51.
HANDICAP_ROWS = (3, 5)


def make_board(first_attacker_is_left=True):
    board = [[random.randrange(NUM_COLORS) for _ in range(GRID_COLS)]
             for _ in range(GRID_ROWS)]
    frontier = [STARTING_FRONTIER] * GRID_ROWS
    for r in HANDICAP_ROWS:
        frontier[r] = STARTING_FRONTIER + (-1 if first_attacker_is_left else 1)
    return BoardState(board=board, frontier=frontier)


# ---------------------------------------------------------------------------
# Formation detection
# ---------------------------------------------------------------------------
# NOTE: cols[0] = FRONT soldier (closest to enemy frontier).
#       cols[-1] = BACK soldier (furthest from enemy).
# ---------------------------------------------------------------------------

@dataclass
class AttackFormation:
    formation_id: int
    row: int
    cols: list          # cols[0]=front, cols[-1]=back

    @property
    def power(self):    # 3 soldiers → 1 kill, 4 → 2, etc.
        return len(self.cols) - 2

    def front_col(self):
        return self.cols[0]


@dataclass
class DefenseFormation:
    formation_id: int
    cells: list         # [(r,c), ...]

    def cell_set(self):
        return set(self.cells)


def find_attack_formations(bs: BoardState, attacker_is_left: bool,
                           chosen: tuple) -> list:
    """Horizontal runs ≥3 in attacker territory, all colors in chosen."""
    out, fid = [], 0
    for r in range(GRID_ROWS):
        lo, hi = (0, bs.frontier[r]) if attacker_is_left \
                 else (bs.frontier[r]+1, GRID_COLS-1)
        if lo > hi:
            continue
        run = []
        for c in range(lo, hi+1):
            if bs.board[r][c] in chosen:
                run.append(c)
            else:
                if len(run) >= 3:
                    # cols[0]=front: for left attacker front=rightmost col
                    ordered = list(reversed(run)) if attacker_is_left else run[:]
                    out.append(AttackFormation(fid, r, ordered))
                    fid += 1
                run = []
        if len(run) >= 3:
            ordered = list(reversed(run)) if attacker_is_left else run[:]
            out.append(AttackFormation(fid, r, ordered))
            fid += 1
    return out


def find_defense_formations(bs: BoardState, defender_is_left: bool,
                            chosen: tuple) -> list:
    """Vertical runs ≥3 in defender territory, all colors in chosen."""
    out, fid = [], 0
    for c in range(GRID_COLS):
        run = []
        for r in range(GRID_ROWS):
            ok = (bs.owner_is_left(r, c) == defender_is_left) and \
                 (bs.board[r][c] in chosen)
            if ok:
                run.append(r)
            else:
                if len(run) >= 3:
                    out.append(DefenseFormation(fid, [(rr, c) for rr in run]))
                    fid += 1
                run = []
        if len(run) >= 3:
            out.append(DefenseFormation(fid, [(rr, c) for rr in run]))
            fid += 1
    return out


# ---------------------------------------------------------------------------
# Formation animation info (precomputed pixel events)
# ---------------------------------------------------------------------------

@dataclass
class FmtAnimInfo:
    fmt_idx: int
    row: int
    initial_cols: list       # cols[0]=front
    attacker_is_left: bool
    frontier_at_start: int   # frontier[row] when this formation begins
    # march events in order:
    #   ("kill", col) - a soldier is spent killing the defender there
    #   ("hole", col) - free advance through a tile already emptied by an
    #                   earlier flank this turn
    events: list
    blocked_by: Optional[int]
    breakthrough: bool
    # flank_cells[k] = defender cells killed by the flanking cascade at the
    # moment the (k+1)-th forward kill lands (no territory changes hands
    # until after all attacks). flank_dfids[k] = defense formations hit
    # (weakened) by the cascade at that moment.
    flank_cells: list = field(default_factory=list)
    flank_dfids: list = field(default_factory=list)

    frontier_cross_px: float = field(init=False)
    event_px: list = field(init=False)
    kills: int = field(init=False)
    end_px: float = field(init=False)

    def __post_init__(self):
        front = self.initial_cols[0]
        f = self.frontier_at_start
        if self.attacker_is_left:
            cells_to_frontier = f - front + 1
        else:
            cells_to_frontier = front - f
        self.frontier_cross_px = cells_to_frontier * CELL

        self.kills = sum(1 for kind, _ in self.events if kind == "kill")
        px = self.frontier_cross_px
        self.event_px = []
        for kind, _ in self.events:
            px += CELL if kind == "hole" else 2 * CELL
            self.event_px.append(px)

        power = len(self.initial_cols) - 2
        has_remaining_soldiers = self.kills < power

        if self.blocked_by is not None or (self.breakthrough and has_remaining_soldiers):
            self.end_px = px + 2 * CELL
        elif self.event_px:
            self.end_px = self.event_px[-1]
        else:
            self.end_px = self.frontier_cross_px

    def soldiers_at(self, slide_px: float) -> int:
        n = len(self.initial_cols)
        if slide_px >= self.end_px and \
                (self.blocked_by is not None or self.breakthrough or self.kills == n-2):
            return 0
        if slide_px < self.frontier_cross_px:
            return n
        removed = 2
        for px, (kind, _) in zip(self.event_px, self.events):
            if kind == "kill" and slide_px >= px:
                removed += 1
        return max(0, n - removed)


# ---------------------------------------------------------------------------
# Combat Plan
# ---------------------------------------------------------------------------

@dataclass
class CombatPlan:
    attacker_is_left: bool
    # sorted execution order: top→bottom row, front-to-back within row
    attack_formations: list
    defense_formations: list
    defense_map: dict           # fid → DefenseFormation
    fmt_anim: list              # FmtAnimInfo per formation (same order)
    hit_def_ids: set
    breakthrough_rows: list
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
    # Attacker cells overrun by a defender reclaim triangle (killed in RECLAIM)
    reclaimed_cells: set


# ---------------------------------------------------------------------------
# Combat plan builder
# ---------------------------------------------------------------------------

def build_combat_plan(bs: BoardState, attacker_is_left: bool,
                      atk_colors: tuple, def_colors: tuple) -> CombatPlan:
    defender_is_left = not attacker_is_left
    orig = bs.copy()

    atk_fmts = find_attack_formations(orig, attacker_is_left, atk_colors)
    def_fmts  = find_defense_formations(orig, defender_is_left, def_colors)
    def_map   = {f.formation_id: f for f in def_fmts}

    cell_to_dfid: dict = {}
    for df in def_fmts:
        for cell in df.cells:
            cell_to_dfid[cell] = df.formation_id

    def sort_key(af: AttackFormation):
        front = af.cols[0]
        return (af.row, -front if attacker_is_left else front)

    atk_fmts_sorted = sorted(atk_fmts, key=sort_key)

    fmt_cell_set: set = set()
    for af in atk_fmts_sorted:
        for c in af.cols:
            fmt_cell_set.add((af.row, c))

    if attacker_is_left:
        non_fmt_atk = [(r, c) for r in range(GRID_ROWS) for c in range(GRID_COLS)
                       if orig.owner_is_left(r, c) and (r, c) not in fmt_cell_set]
    else:
        non_fmt_atk = [(r, c) for r in range(GRID_ROWS) for c in range(GRID_COLS)
                       if not orig.owner_is_left(r, c) and (r, c) not in fmt_cell_set]

    sim_frontier = orig.frontier[:]
    hit_def_ids: set = set()
    breakthrough_rows: list = []
    fmt_anim_list: list = []
    row_breakthrough_count: dict = {}

    atk_removed: set = set(fmt_cell_set)
    def_removed: set = set()
    flank_holes: set = set()    # flank-killed cells whose tiles stay defender's

    for idx, af in enumerate(atk_fmts_sorted):
        r = af.row
        power             = af.power
        frontier_at_start = sim_frontier[r]
        remaining_power   = power
        events: list = []
        blocked_by: Optional[int] = None
        breakthrough = False

        # --- Main row: forward march. Killing a defender costs a soldier;
        #     tiles already emptied by an earlier flank are claimed for free.
        while remaining_power > 0:
            if attacker_is_left:
                next_def_col = sim_frontier[r] + 1
                past_edge    = next_def_col >= GRID_COLS
            else:
                next_def_col = sim_frontier[r]
                past_edge    = next_def_col < 0

            if past_edge:
                breakthrough = True
                break

            cell = (r, next_def_col)
            if cell in def_removed:
                events.append(("hole", next_def_col))
                sim_frontier[r] += 1 if attacker_is_left else -1
                continue
            if cell in cell_to_dfid:
                did = cell_to_dfid[cell]
                hit_def_ids.add(did)
                blocked_by = did
                break
            events.append(("kill", next_def_col))
            def_removed.add(cell)
            sim_frontier[r] += 1 if attacker_is_left else -1
            remaining_power -= 1

        kill_cols = [col for kind, col in events if kind == "kill"]
        kills = len(kill_cols)

        # --- Flanking cascade: each forward kill after the first extends the
        #     cascade one row further out on every earlier kill column, so
        #     after K kills column j has killed defenders out to K-j rows
        #     above and below. Flanked defenders die but their tiles stay
        #     defender-owned until after all attacks. The cascade stops in a
        #     direction when it meets anything that is not a living defender;
        #     hitting a defense formation also stops it, but counts as a hit
        #     (weakened now, destroyed at cleanup).
        flank_cells: list = [[] for _ in range(kills)]
        flank_dfids: list = [[] for _ in range(kills)]
        stopped: dict = {}
        for m in range(1, kills):          # 0-based index of the trigger kill
            for j in range(m):             # earlier kill columns
                s = m - j                  # cascade distance at this moment
                cc = kill_cols[j]
                for d in (1, -1):
                    if stopped.get((j, d)):
                        continue
                    rr = r + d * s
                    if 0 <= rr < GRID_ROWS:
                        is_def = cc > sim_frontier[rr] if attacker_is_left \
                            else cc <= sim_frontier[rr]
                    else:
                        is_def = False
                    vcell = (rr, cc)
                    if is_def and vcell in cell_to_dfid:
                        did = cell_to_dfid[vcell]
                        hit_def_ids.add(did)
                        flank_dfids[m].append(did)
                        stopped[(j, d)] = True
                        continue
                    if not is_def or vcell in def_removed:
                        stopped[(j, d)] = True
                        continue
                    flank_cells[m].append(vcell)
                    def_removed.add(vcell)
                    flank_holes.add(vcell)

        if breakthrough:
            row_breakthrough_count[r] = row_breakthrough_count.get(r, 0) + 1

        fmt_anim_list.append(FmtAnimInfo(
            fmt_idx=idx, row=r,
            initial_cols=af.cols[:],
            attacker_is_left=attacker_is_left,
            frontier_at_start=frontier_at_start,
            events=events,
            blocked_by=blocked_by,
            breakthrough=breakthrough,
            flank_cells=flank_cells,
            flank_dfids=flank_dfids,
        ))

    # --- Flank holes left unclaimed after all attacks: the defenders in those
    #     rows fall back to fill the gaps, and the freed tiles at the front of
    #     the row flip to the attacker.
    flank_rows: dict = {}
    for (rr, cc) in flank_holes:
        still_def = cc > sim_frontier[rr] if attacker_is_left \
            else cc <= sim_frontier[rr]
        if still_def:
            flank_rows[rr] = flank_rows.get(rr, 0) + 1
    for rr, h in flank_rows.items():
        sim_frontier[rr] += h if attacker_is_left else -h

    # --- Breakthrough reclaim: the defender re-takes a triangle of tiles
    #     centered on the hit row. Each row is brought UP TO a target depth
    #     (4 tiles in the hit row, decreasing by one per row of distance:
    #     3 in rows ±1, 2 in rows ±2, 1 in rows ±3) — i.e. the defender ends
    #     up owning max(target, what it already owns) tiles in that row.
    #     Rows it already holds deeply are left untouched. ---
    RECLAIM_TRIANGLE = ((0, 4), (1, 3), (-1, 3), (2, 2), (-2, 2),
                        (3, 1), (-3, 1))
    for r, count in row_breakthrough_count.items():
        for _ in range(count):
            breakthrough_rows.append(r)
            for ds, target in RECLAIM_TRIANGLE:
                rr = r + ds
                if not (0 <= rr < GRID_ROWS):
                    continue
                if attacker_is_left:
                    # defender = right, owns cols frontier+1..9; owning `target`
                    # tiles means frontier at GRID_COLS-1-target. Only move it
                    # back (max-with-current), never forward.
                    sim_frontier[rr] = min(sim_frontier[rr],
                                           GRID_COLS - 1 - target)
                else:
                    # defender = left, owns cols 0..frontier; owning `target`
                    # tiles means frontier at target-1.
                    sim_frontier[rr] = max(sim_frontier[rr], target - 1)

    for did in hit_def_ids:
        for cell in def_map[did].cells:
            def_removed.add(cell)

    # Cells that flipped from attacker back to defender (reclaim triangle).
    reclaimed_cells: set = set()
    for r in range(GRID_ROWS):
        orig_f = orig.frontier[r]
        new_f  = sim_frontier[r]
        if attacker_is_left:
            for c in range(new_f + 1, orig_f + 1):
                reclaimed_cells.add((r, c))
        else:
            for c in range(orig_f + 1, new_f + 1):
                reclaimed_cells.add((r, c))
    # Attacker units standing on reclaimed tiles are overrun.
    atk_removed |= reclaimed_cells

    final = orig.copy()
    final.frontier = sim_frontier[:]
    if attacker_is_left:
        final.hp_right -= len(breakthrough_rows)
    else:
        final.hp_left -= len(breakthrough_rows)

    # Rows whose ownership shifted (captures advanced or reclaim retreated it).
    changed_rows = set(r for r in range(GRID_ROWS)
                       if sim_frontier[r] != orig.frontier[r])

    # --- Attacker gravity (per row) ---
    atk_slide_map: dict = {}
    atk_spawn_cells: list = []
    affected_atk_rows = set(af.row for af in atk_fmts_sorted) | changed_rows

    for r in affected_atk_rows:
        f = sim_frontier[r]

        if attacker_is_left:
            orig_atk_cols = list(range(0, orig.frontier[r] + 1))
            new_territory  = list(range(0, f + 1))
        else:
            orig_atk_cols = list(range(orig.frontier[r] + 1, GRID_COLS))
            new_territory  = list(range(f + 1, GRID_COLS))

        survivors = [c for c in orig_atk_cols
                     if (r, c) not in atk_removed
                     and (r, c) not in def_removed]

        n = min(len(survivors), len(new_territory))
        survivors = survivors[-n:] if attacker_is_left else survivors[:n]

        if attacker_is_left:
            new_pos = new_territory[-n:]
            for old_c, new_c in zip(reversed(survivors), reversed(new_pos)):
                atk_slide_map[(r, old_c)] = (r, new_c)
                final.board[r][new_c] = orig.board[r][old_c]
            for c in (new_territory[:-n] if n else new_territory):
                final.board[r][c] = random.randrange(NUM_COLORS)
                atk_spawn_cells.append((r, c))
        else:
            new_pos = new_territory[:n]
            for old_c, new_c in zip(survivors, new_pos):
                atk_slide_map[(r, old_c)] = (r, new_c)
                final.board[r][new_c] = orig.board[r][old_c]
            for c in new_territory[n:]:
                final.board[r][c] = random.randrange(NUM_COLORS)
                atk_spawn_cells.append((r, c))

    # --- Defender gravity (per row) ---
    def_slide_map: dict = {}
    def_spawn_cells: list = []
    affected_def_rows = set(r for (r, c) in def_removed) | \
                        set(r for (r, c) in reclaimed_cells) | changed_rows

    for r in affected_def_rows:
        f = sim_frontier[r]
        if defender_is_left:
            region = list(range(0, f + 1))
            orig_def_region = list(range(0, orig.frontier[r] + 1))
        else:
            region = list(range(f + 1, GRID_COLS))
            orig_def_region = list(range(orig.frontier[r] + 1, GRID_COLS))

        survivors = [c for c in orig_def_region
                     if (r, c) not in def_removed and c in region]

        if r in flank_rows:
            # flanked row: defenders fall back toward their own edge to fill
            # the gaps (units in front of a hole retreat out of the ceded
            # tiles, so don't filter by the final region); any spawns man
            # what is left of the front
            survivors = [c for c in orig_def_region
                         if (r, c) not in def_removed]
            if defender_is_left:
                new_pos = list(range(0, len(survivors)))
                spawn_cols = range(len(survivors), f + 1)
            else:
                new_pos = list(range(GRID_COLS - len(survivors), GRID_COLS))
                spawn_cols = range(f + 1, GRID_COLS - len(survivors))
            for old_c, new_c in zip(survivors, new_pos):
                def_slide_map[(r, old_c)] = (r, new_c)
                final.board[r][new_c] = orig.board[r][old_c]
            for c in spawn_cols:
                final.board[r][c] = random.randrange(NUM_COLORS)
                def_spawn_cells.append((r, c))
        elif defender_is_left:
            new_pos = list(range(f - len(survivors) + 1, f + 1))
            for old_c, new_c in zip(reversed(survivors), reversed(new_pos)):
                def_slide_map[(r, old_c)] = (r, new_c)
                final.board[r][new_c] = orig.board[r][old_c]
            for c in range(0, f - len(survivors) + 1):
                final.board[r][c] = random.randrange(NUM_COLORS)
                def_spawn_cells.append((r, c))
        else:
            new_pos = list(range(f + 1, f + 1 + len(survivors)))
            for old_c, new_c in zip(survivors, new_pos):
                def_slide_map[(r, old_c)] = (r, new_c)
                final.board[r][new_c] = orig.board[r][old_c]
            for c in range(f + 1 + len(survivors), GRID_COLS):
                final.board[r][c] = random.randrange(NUM_COLORS)
                def_spawn_cells.append((r, c))

    for (r, c) in reclaimed_cells:
        final.board[r][c] = random.randrange(NUM_COLORS)
        if (r, c) not in def_spawn_cells:
            def_spawn_cells.append((r, c))

    return CombatPlan(
        attacker_is_left=attacker_is_left,
        attack_formations=atk_fmts_sorted,
        defense_formations=def_fmts,
        defense_map=def_map,
        fmt_anim=fmt_anim_list,
        hit_def_ids=hit_def_ids,
        breakthrough_rows=breakthrough_rows,
        final_board=final,
        non_fmt_atk_cells=non_fmt_atk,
        atk_slide_map=atk_slide_map,
        def_slide_map=def_slide_map,
        atk_spawn_cells=atk_spawn_cells,
        def_spawn_cells=def_spawn_cells,
        reclaimed_cells=reclaimed_cells,
    )


# ---------------------------------------------------------------------------
# Computer player
# ---------------------------------------------------------------------------
# The AI evaluates every possible element pair by simulating the turn with
# the real combat engine (build_combat_plan) and scoring the outcome with a
# small weight vector. The weights ship in assets/ai_weights.json, produced
# by offline self-play training (train_ai.py); built-in defaults are used if
# the file is missing. Everything runs locally — no network.
#
# Difficulty is deliberately "medium": instead of always playing its best
# move, the AI picks from its top moves with fixed probabilities.
# ---------------------------------------------------------------------------

ALL_COMBOS = list(itertools.combinations(range(NUM_COLORS), 2))

AI_WEIGHTS = {
    "tiles": 1.0,    # net territory gained
    "hp":    6.0,    # breakthrough damage to the enemy player
    "kill":  0.35,   # defenders killed
    "loss":  0.25,   # own soldiers spent
    "fmt":   0.6,    # enemy defense formations destroyed
}


def load_ai_weights():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "assets", "ai_weights.json")
    try:
        with open(path) as f:
            data = json.load(f)
        AI_WEIGHTS.update({k: float(v) for k, v in data.items()
                           if k in AI_WEIGHTS})
    except (OSError, ValueError):
        pass


def plan_score(plan, orig_bs, attacker_is_left, w=None):
    """Score a simulated turn from the ATTACKER's point of view."""
    w = w or AI_WEIGHTS
    sign = 1 if attacker_is_left else -1
    tiles = sum((plan.final_board.frontier[r] - orig_bs.frontier[r]) * sign
                for r in range(GRID_ROWS))
    if attacker_is_left:
        hp_dmg = orig_bs.hp_right - plan.final_board.hp_right
    else:
        hp_dmg = orig_bs.hp_left - plan.final_board.hp_left
    def_killed = sum(f.kills + sum(len(fc) for fc in f.flank_cells)
                     for f in plan.fmt_anim)
    def_killed += sum(len(plan.defense_map[d].cells)
                      for d in plan.hit_def_ids)
    atk_lost = sum(len(af.cols) for af in plan.attack_formations)
    return (w["tiles"] * tiles + w["hp"] * hp_dmg + w["kill"] * def_killed
            - w["loss"] * atk_lost + w["fmt"] * len(plan.hit_def_ids))


# Difficulty: every tier uses the same trained evaluation; they differ in
# how faithfully they follow it. Probabilities over the RANKED moves only
# (best, 2nd, ...) — no tier ever plays a random move, and none ever plays
# worse than its listed depth (4th-best at Easy, 3rd at Medium, 2nd at Hard).
AI_LEVELS = {
    "easy":   (0.35, 0.30, 0.20, 0.15),
    "medium": (0.55, 0.28, 0.17),
    "hard":   (0.85, 0.15),
}


def _pick_ranked(scored, rng, level):
    """scored: [(score, combo)]. level None = pure argmax (training)."""
    scored.sort(key=lambda s: -s[0])
    if level is None:
        return scored[0][1]
    r = rng.random()
    acc = 0.0
    dist = AI_LEVELS[level]
    for i, p in enumerate(dist):
        acc += p
        if r < acc or i == len(dist) - 1 or i == len(scored) - 1:
            return scored[min(i, len(scored) - 1)][1]
    return scored[0][1]


def ai_choose_attack(bs, attacker_is_left, weights=None, rng=random,
                     level="medium", sample_defs=None):
    """Pick attack elements: each pair is judged by its average outcome over
    the defenses the opponent might choose (unknown at attack time)."""
    defs = ALL_COMBOS if sample_defs is None \
        else rng.sample(ALL_COMBOS, sample_defs)
    scored = []
    for atk in ALL_COMBOS:
        tot = 0.0
        for dfn in defs:
            plan = build_combat_plan(bs.copy(), attacker_is_left, atk, dfn)
            tot += plan_score(plan, bs, attacker_is_left, weights)
        scored.append((tot / len(defs), atk))
    return _pick_ranked(scored, rng, level)


def ai_choose_defense(bs, attacker_is_left, weights=None, rng=random,
                      level="medium", sample_atks=None):
    """Pick defense elements BLIND — the defender never sees the attack, so
    each pair is judged by its average outcome over the attacks the
    opponent might have chosen."""
    atks = ALL_COMBOS if sample_atks is None \
        else rng.sample(ALL_COMBOS, sample_atks)
    scored = []
    for dfn in ALL_COMBOS:
        tot = 0.0
        for atk in atks:
            plan = build_combat_plan(bs.copy(), attacker_is_left, atk, dfn)
            tot -= plan_score(plan, bs, attacker_is_left, weights)
        scored.append((tot / len(atks), dfn))
    return _pick_ranked(scored, rng, level)


# ---------------------------------------------------------------------------
# Animation state machine
# ---------------------------------------------------------------------------

class Phase(Enum):
    FADE_SETUP    = auto()
    FMT_SLIDE     = auto()
    CLEANUP       = auto()
    RECLAIM       = auto()
    SLIDE_FILL    = auto()
    SPAWN         = auto()
    VICTORY       = auto()
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
        self.orig  = orig_board
        self.phase = Phase.FADE_SETUP
        self.t     = 0.0

        self.dead: set = set()
        self.total_t: float = 0.0
        # (r,c) -> (total_t, death_cx) — x position where the unit died
        self.dying: dict = {}
        self.vis_frontier: list = orig_board.frontier[:]
        # live HP shown during the animation: drops the moment a
        # breakthrough lands (with the red flash), not at the end
        self.hp_left: int = orig_board.hp_left
        self.hp_right: int = orig_board.hp_right
        # set when a breakthrough brings a player to 0 HP: the match ends
        # right away (after a short beat for the flash) — remaining attacks
        # and the wind-down phases are skipped in favor of the victory sweep
        self.match_over_at: Optional[float] = None
        self.victory: Optional[dict] = None
        # once a breakthrough lands, the attackers celebrate through the
        # flash and keep it up while their army fades back in
        self.breakthrough_happened: bool = False

        self.non_fmt_alpha: float = 255.0
        self.def_outline_alpha: float = 0.0
        self.red_def_ids: set = set()

        self.cur_fmt_idx: int = 0
        self.cur_slide_px: float = 0.0
        self.fmt_tf_t: float = 0.0     # transform timer for current formation

        self.slide_progress: float = 0.0
        self.spawn_alpha: float = 0.0
        self.flash_alpha: float = 0.0
        self._reclaim_start_frontier: list = []

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def kill_cell(self, r, c, death_cx=None):
        self.dead.add((r, c))
        if (r, c) not in self.dying:
            if death_cx is None:
                death_cx = c * CELL + CELL // 2
            self.dying[(r, c)] = (self.total_t, death_cx)

    def _advance_frontier(self, r, kills_delta):
        if self.plan.attacker_is_left:
            self.vis_frontier[r] += kills_delta
        else:
            self.vis_frontier[r] -= kills_delta

    def _trigger_fmt_events(self, fai: FmtAnimInfo, old_px: float, new_px: float):
        r = fai.row
        d = 1 if fai.attacker_is_left else -1

        def _slid_cx(c, slide_px):
            return int(c * CELL + int(slide_px * d) + CELL // 2)

        if old_px < fai.frontier_cross_px <= new_px:
            for c in fai.initial_cols[:2]:
                self.kill_cell(r, c, _slid_cx(c, fai.frontier_cross_px))
            play_sfx("sacrifice")

        kill_i = 0
        for px, (kind, def_col) in zip(fai.event_px, fai.events):
            if kind == "hole":
                # marching through a tile an earlier flank emptied: claim it
                if old_px < px <= new_px:
                    self._advance_frontier(r, 1)
                continue
            if old_px < px <= new_px:
                soldier_idx = 2 + kill_i
                atk_c = fai.initial_cols[min(soldier_idx,
                                             len(fai.initial_cols) - 1)]
                if soldier_idx < len(fai.initial_cols):
                    self.kill_cell(r, atk_c, _slid_cx(atk_c, px))
                self.kill_cell(r, def_col)
                self._advance_frontier(r, 1)
                # the killer's element voices the kill
                play_sfx(KILL_SFX[self.orig.board[r][atk_c]])
                # the flanking cascade fires the moment this kill lands;
                # flanked defenders die but their tiles stay defender-owned
                for (vr, vc) in fai.flank_cells[kill_i]:
                    self.kill_cell(vr, vc)
                for did in fai.flank_dfids[kill_i]:
                    self.red_def_ids.add(did)
            kill_i += 1

        if fai.blocked_by is not None:
            if old_px < fai.end_px <= new_px:
                for c in fai.initial_cols:
                    self.kill_cell(r, c, _slid_cx(c, fai.end_px))
                # first hit: the wall turns weakened, its mages spent
                self.red_def_ids.add(fai.blocked_by)
                play_sfx("block")

        if fai.breakthrough:
            if old_px < fai.end_px <= new_px:
                for c in fai.initial_cols:
                    self.kill_cell(r, c, _slid_cx(c, fai.end_px))
                self.flash_alpha = 220.0
                self.breakthrough_happened = True
                play_sfx("breakthrough")
                # the defender loses the hit point right now
                if self.plan.attacker_is_left:
                    self.hp_right -= 1
                else:
                    self.hp_left -= 1
                if min(self.hp_left, self.hp_right) <= 0 and \
                        self.match_over_at is None:
                    self.match_over_at = self.total_t + 0.7


# ---------------------------------------------------------------------------
# Animation update
# ---------------------------------------------------------------------------

def advance_anim(state: AnimState, dt: float) -> bool:
    plan = state.plan
    state.total_t += dt

    if state.flash_alpha > 0:
        state.flash_alpha = max(0.0, state.flash_alpha - dt * 280)

    # a killing blow ends the match on the spot: freeze the state machine
    # (the flash and death bursts still play out) for a beat, then run the
    # victory sweep — the loser's army explodes and the winner floods in
    if state.match_over_at is not None and state.phase != Phase.VICTORY:
        if state.total_t >= state.match_over_at:
            _start_victory(state)
        else:
            return False

    if state.phase == Phase.VICTORY:
        prev_t = state.t
        state.t += dt
        # each of the loser's units pops as its explosion fires
        if state.victory and any(prev_t < te <= state.t
                                 for te in state.victory["explode"].values()):
            play_sfx("poof")
        if state.t >= VICTORY_DUR:
            state.phase = Phase.DONE
        return state.phase == Phase.DONE

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
        for r in range(GRID_ROWS):
            start_f = state._reclaim_start_frontier[r]
            final_f = plan.final_board.frontier[r]
            if start_f != final_f:
                state.vis_frontier[r] = int(round(start_f + (final_f - start_f) * p))
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
    state.fmt_tf_t = 0.0
    if not state.plan.attack_formations:
        _start_cleanup(state)


def _update_fmt_slide(state: AnimState, dt: float):
    plan = state.plan
    if state.cur_fmt_idx >= len(plan.attack_formations):
        _start_cleanup(state)
        return

    # the formation transforms into element balls before it marches
    if state.fmt_tf_t < TRANSFORM_DUR:
        if state.fmt_tf_t == 0.0:
            play_sfx("transform")
        state.fmt_tf_t += dt
        return

    fai = plan.fmt_anim[state.cur_fmt_idx]
    old_px  = state.cur_slide_px
    new_px  = old_px + dt * DUR_FMT_SPEED
    state._trigger_fmt_events(fai, old_px, new_px)
    state.cur_slide_px = new_px

    if new_px >= fai.end_px:
        state.cur_fmt_idx += 1
        state.cur_slide_px = 0.0
        state.fmt_tf_t = 0.0
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
    if state.plan.breakthrough_rows:
        state.phase = Phase.RECLAIM
        state.t = 0.0
        state._reclaim_start_frontier = state.vis_frontier[:]
        for (r, c) in state.plan.reclaimed_cells:
            state.kill_cell(r, c)
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


# Victory sweep timeline (seconds within Phase.VICTORY):
#   0.0-1.1  the loser's remaining troops panic and explode in a wave
#            rolling from their front line to their back edge
#   1.1-2.0  the winner's troops march forward to occupy the whole board
#   2.0-2.6  reinforcements fade in behind them
#   2.6-4.4  the whole army celebrates
VICT_MARCH0 = 1.1
VICT_MARCH1 = 2.0
VICT_SPAWN0 = 2.0
VICT_SPAWN1 = 2.6
VICTORY_DUR = 4.4


def _start_victory(state: AnimState):
    """The defender just died: schedule their army's explosions, and plan
    the winner's board-wide advance. Also rewrites plan.final_board so the
    game-over backdrop shows the occupied board."""
    plan, orig = state.plan, state.orig
    atk_left = plan.attacker_is_left
    final = plan.final_board
    explode: dict = {}
    moves: list = []
    spawns: list = []

    for r in range(GRID_ROWS):
        f = state.vis_frontier[r]
        # surviving defenders: explosion wave front-to-back
        def_cols = range(f + 1, GRID_COLS) if atk_left else range(0, f + 1)
        front = f + 1 if atk_left else f
        for c in def_cols:
            if (r, c) in state.dead:
                continue
            dist = (c - front) if atk_left else (front - c)
            jitter = ((r * 7 + c * 13) % 4) * 0.05
            explode[(r, c)] = 0.1 + dist * 0.09 + jitter

        # surviving attackers pack against the defender's back edge
        atk_cols = range(0, f + 1) if atk_left else range(f + 1, GRID_COLS)
        alive = [c for c in atk_cols if (r, c) not in state.dead]
        n = len(alive)
        targets = list(range(GRID_COLS - n, GRID_COLS)) if atk_left \
            else list(range(0, n))
        spawn_cols = range(0, GRID_COLS - n) if atk_left \
            else range(n, GRID_COLS)
        for c_from, c_to in zip(alive, targets):
            ci = orig.board[r][c_from]
            moves.append((r, c_from, c_to, ci))
            final.board[r][c_to] = ci
        for c in spawn_cols:
            ci = random.randrange(NUM_COLORS)
            spawns.append((r, c, ci))
            final.board[r][c] = ci
        final.frontier[r] = GRID_COLS - 1 if atk_left else -1

    state.victory = {"explode": explode, "moves": moves, "spawns": spawns,
                     "start_frontier": state.vis_frontier[:]}
    state.phase = Phase.VICTORY
    state.t = 0.0


def _draw_victory(surf, state: AnimState, anim_t=0.0):
    plan = state.plan
    orig = state.orig
    v = state.victory
    t = state.t
    atk_left = plan.attacker_is_left
    atk_side = "left" if atk_left else "right"
    def_side = "right" if atk_left else "left"

    # territory floods forward with the march
    p = 0.0 if t <= VICT_MARCH0 else \
        min(1.0, (t - VICT_MARCH0) / (VICT_MARCH1 - VICT_MARCH0))
    full = GRID_COLS - 1 if atk_left else -1
    frontier = [int(round(s + (full - s) * p))
                for s in v["start_frontier"]]
    draw_bg(surf, frontier)

    # the doomed army panics, then explodes in a wave
    scared_f = int(state.total_t / 0.12) % 2
    for (r, c), te in v["explode"].items():
        ci = orig.board[r][c]
        if t < te:
            x, y = cell_xy(r, c)
            draw_mage(surf, ci, def_side, x, y,
                      pose="scared", pose_frame=scared_f)
        elif t - te < DEATH_DUR:
            draw_death_burst(surf, ci, c * CELL + CELL // 2,
                             r * CELL + GRID_Y + CELL // 2, t - te)

    # once the board is theirs, the whole army celebrates
    party = t >= VICT_SPAWN1
    cel_f = int(state.total_t / 0.14) % 2

    # the winners march in...
    alpha = int(min(255, 255 * t / 0.4))
    for (r, c0, c1, ci) in v["moves"]:
        px = int((c0 + (c1 - c0) * p) * CELL)
        if party:
            draw_mage(surf, ci, atk_side, px, r * CELL + GRID_Y, alpha,
                      pose="celebrate", pose_frame=cel_f)
        else:
            draw_mage(surf, ci, atk_side, px, r * CELL + GRID_Y, alpha, anim_t)

    # ...and their reinforcements appear behind them
    if t >= VICT_SPAWN0:
        sa = int(min(255, 255 * (t - VICT_SPAWN0)
                     / (VICT_SPAWN1 - VICT_SPAWN0)))
        for (r, c, ci) in v["spawns"]:
            x, y = cell_xy(r, c)
            if party:
                draw_mage(surf, ci, atk_side, x, y, sa,
                          pose="celebrate", pose_frame=cel_f)
            else:
                draw_mage(surf, ci, atk_side, x, y, sa, anim_t)

    draw_frontier_dots(surf, frontier)
    draw_grid(surf)

    if state.flash_alpha > 0:
        ov = pygame.Surface((GRID_COLS * CELL, GRID_ROWS * CELL), pygame.SRCALPHA)
        ov.fill((255, 0, 0, int(min(state.flash_alpha, 160))))
        surf.blit(ov, (0, GRID_Y))


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

DEATH_DUR = 0.38


def draw_death_burst(surf, color_idx, cx, cy, age):
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
            side = "left" if c <= frontier[r] else "right"
            if BG_TILES:
                surf.blit(BG_TILES[side][BG_MAP[r][c]],
                          (c * CELL, r * CELL + GRID_Y))
            else:
                color = BG_LEFT if side == "left" else BG_RIGHT
                pygame.draw.rect(surf, color, cell_rect(r, c))


def draw_frontier_dots(surf, frontier):
    for r in range(GRID_ROWS):
        f = frontier[r]
        if f < 0 or f >= GRID_COLS - 1:
            continue
        x  = (f + 1) * CELL
        y0 = r * CELL + GRID_Y + 5
        y1 = (r + 1) * CELL + GRID_Y - 5
        y = y0
        while y < y1:
            pygame.draw.line(surf, C_FRONTIER, (x, y), (x, min(y+5, y1)), 2)
            y += 9


def draw_grid(surf):
    for r in range(GRID_ROWS + 1):
        y = r * CELL + GRID_Y
        pygame.draw.line(surf, C_GRID, (0, y), (GRID_COLS * CELL, y), 1)
    for c in range(GRID_COLS + 1):
        x = c * CELL
        pygame.draw.line(surf, C_GRID, (x, GRID_Y), (x, GRID_ROWS * CELL + GRID_Y), 1)


def draw_static(surf, bs: BoardState, active_left=None, anim_t=0.0, glow=None,
                celebrate_t=None):
    draw_bg(surf, bs.frontier)
    if glow is not None:
        glow.draw(surf, bs)          # ground rings under the units
    for r in range(GRID_ROWS):
        for c in range(GRID_COLS):
            ci = bs.board[r][c]
            is_left = bs.owner_is_left(r, c)
            side = "left" if is_left else "right"
            x, y = cell_xy(r, c)
            if celebrate_t is not None:
                draw_mage(surf, ci, side, x, y, pose="celebrate",
                          pose_frame=int(celebrate_t / 0.14) % 2)
            else:
                t = anim_t if (active_left is None or
                               is_left == active_left) else 0.0
                draw_mage(surf, ci, side, x, y, anim_t=t)
    draw_frontier_dots(surf, bs.frontier)
    draw_grid(surf)


def compute_scared_cells(state: AnimState):
    """Defenders about to die panic once their killer is one tile away:
    the kill target in the marching row, and the flank victims whose
    cascade fires on that same kill. A breakthrough's red flash panics the
    entire defending army for as long as the screen flashes."""
    plan = state.plan
    scared = set()

    if state.flash_alpha > 0:
        atk_left = plan.attacker_is_left
        for r in range(GRID_ROWS):
            f = state.vis_frontier[r]
            cols = range(f + 1, GRID_COLS) if atk_left else range(0, f + 1)
            for c in cols:
                if (r, c) not in state.dead:
                    scared.add((r, c))

    if state.phase != Phase.FMT_SLIDE or state.fmt_tf_t < TRANSFORM_DUR or \
            state.cur_fmt_idx >= len(plan.fmt_anim):
        return scared
    fai = plan.fmt_anim[state.cur_fmt_idx]
    kill_i = 0
    for px, (kind, col) in zip(fai.event_px, fai.events):
        if kind != "kill":
            continue
        if px - CELL <= state.cur_slide_px < px:
            scared.add((fai.row, col))
            scared.update(fai.flank_cells[kill_i])
        kill_i += 1
    return scared


def draw_animated(surf, state: AnimState, anim_t=0.0):
    plan  = state.plan
    orig  = state.orig
    final = plan.final_board
    phase = state.phase

    if phase == Phase.VICTORY:
        _draw_victory(surf, state, anim_t)
        return

    atk_left  = plan.attacker_is_left
    atk_side  = "left" if atk_left else "right"
    def_side  = "right" if atk_left else "left"
    direction = 1 if atk_left else -1

    ball_f  = int(state.total_t / BALL_FRAME_S) % 4
    cast_f  = min(2, int(state.total_t / CAST_FRAME_S))
    tired_f = int(state.total_t / TIRED_FRAME_S) % 2
    field_f = int(state.total_t / FIELD_FRAME_S) % 3
    scared_f = int(state.total_t / 0.12) % 2
    scared_cells = compute_scared_cells(state)
    # a breakthrough: the defenders panic under the flash, and the attackers
    # celebrate — through the flash and then as their army fades back in
    celebrating = state.flash_alpha > 0 or \
        (state.breakthrough_happened and
         phase in (Phase.CLEANUP, Phase.RECLAIM))
    cel_f = int(state.total_t / 0.14) % 2

    draw_bg(surf, state.vis_frontier)

    non_fmt_set = set(plan.non_fmt_atk_cells)
    all_fmt_cells: dict = {}
    for i, af in enumerate(plan.attack_formations):
        for c in af.cols:
            all_fmt_cells[(af.row, c)] = i

    def_cell_map: dict = {}
    for df in plan.defense_formations:
        for cell in df.cells:
            def_cell_map[cell] = df.formation_id

    # defenders hold their casting pose while formations are highlighted
    defenders_casting = state.def_outline_alpha > 0 or phase == Phase.FMT_SLIDE

    def side_of(r, c, board=orig):
        return "left" if board.owner_is_left(r, c) else "right"

    for r in range(GRID_ROWS):
        for c in range(GRID_COLS):
            cell = (r, c)
            ci   = orig.board[r][c]
            side = side_of(r, c)

            # Dead cells — play burst at where the unit actually died
            if cell in state.dead:
                if cell in state.dying:
                    death_time, death_cx = state.dying[cell]
                    age = state.total_t - death_time
                    if age < DEATH_DUR:
                        death_cy = r * CELL + GRID_Y + CELL // 2
                        draw_death_burst(surf, ci, death_cx, death_cy, age)
                continue

            # Doomed defenders panic as their killer closes in
            if cell in scared_cells:
                x, y = cell_xy(r, c)
                draw_mage(surf, ci, def_side, x, y,
                          pose="scared", pose_frame=scared_f)
                continue

            # Non-formation attackers (fade out phase 1, fade in phase 5)
            if cell in non_fmt_set:
                alpha = int(state.non_fmt_alpha)
                if alpha > 0:
                    if phase in (Phase.SLIDE_FILL, Phase.SPAWN, Phase.DONE):
                        if cell in plan.atk_slide_map:
                            nr, nc = plan.atk_slide_map[cell]
                            tp = state.slide_progress
                            px = int((c + (nc - c) * tp) * CELL)
                            draw_mage(surf, ci, side, px, r * CELL + GRID_Y,
                                      255, anim_t)
                        else:
                            x, y = cell_xy(r, c)
                            draw_mage(surf, ci, side, x, y, 255, anim_t)
                    else:
                        x, y = cell_xy(r, c)
                        if celebrating:
                            draw_mage(surf, ci, side, x, y, alpha,
                                      pose="celebrate", pose_frame=cel_f)
                        else:
                            draw_mage(surf, ci, side, x, y, alpha, anim_t)
                continue

            # Attack formation cells
            if cell in all_fmt_cells:
                fi = all_fmt_cells[cell]
                if fi < state.cur_fmt_idx:
                    continue
                elif fi == state.cur_fmt_idx and phase == Phase.FMT_SLIDE:
                    if state.fmt_tf_t < TRANSFORM_DUR:
                        # flash → collapse → orb, in place
                        tf_i = min(2, int(state.fmt_tf_t / TF_FRAME_S))
                        x, y = cell_xy(r, c)
                        draw_mage(surf, ci, atk_side, x, y,
                                  pose="tf", pose_frame=tf_i)
                    else:
                        fai = plan.fmt_anim[fi]
                        n   = fai.soldiers_at(state.cur_slide_px)
                        active = fai.initial_cols[-n:] if n > 0 else []
                        if c in active:
                            px = int(c * CELL + state.cur_slide_px * direction)
                            draw_ball(surf, ci, atk_side, px,
                                      r * CELL + GRID_Y, ball_f)
                else:
                    x, y = cell_xy(r, c)
                    if celebrating:
                        draw_mage(surf, ci, atk_side, x, y,
                                  pose="celebrate", pose_frame=cel_f)
                    else:
                        draw_mage(surf, ci, atk_side, x, y, 255, anim_t)
                continue

            # Defense formation cells
            if cell in def_cell_map:
                did = def_cell_map[cell]
                if cell in plan.def_slide_map and \
                        phase in (Phase.SLIDE_FILL, Phase.SPAWN, Phase.DONE):
                    nr, nc = plan.def_slide_map[cell]
                    if phase == Phase.SLIDE_FILL:
                        tp = state.slide_progress
                        px = int((c + (nc - c) * tp) * CELL)
                        draw_mage(surf, ci, side, px, r * CELL + GRID_Y,
                                  255, anim_t)
                    else:
                        x, y = cell_xy(nr, nc)
                        draw_mage(surf, ci, side, x, y, 255, anim_t)
                else:
                    x, y = cell_xy(r, c)
                    if did in state.red_def_ids:
                        # spent: bowed over behind the weakened wall
                        draw_mage(surf, ci, def_side, x, y,
                                  pose="tired", pose_frame=tired_f)
                    elif defenders_casting:
                        draw_mage(surf, ci, def_side, x, y,
                                  pose="cast", pose_frame=cast_f)
                    else:
                        draw_mage(surf, ci, side, x, y, 255, anim_t)
                continue

            # Attacker slide-fill cells
            if cell in plan.atk_slide_map:
                nr, nc = plan.atk_slide_map[cell]
                if phase == Phase.SLIDE_FILL:
                    tp = state.slide_progress
                    px = int((c + (nc - c) * tp) * CELL)
                    draw_mage(surf, ci, side, px, r * CELL + GRID_Y, 255, anim_t)
                elif phase in (Phase.SPAWN, Phase.DONE):
                    x, y = cell_xy(nr, nc)
                    draw_mage(surf, ci, side, x, y, 255, anim_t)
                else:
                    x, y = cell_xy(r, c)
                    draw_mage(surf, ci, side, x, y, 255, anim_t)
                continue

            # Defender slide-fill cells — a retreating defender is still a
            # defender: keep its own colors and keep it facing the enemy
            # while it slides backward, even if its old tile now belongs to
            # the attacker.
            if cell in plan.def_slide_map:
                nr, nc = plan.def_slide_map[cell]
                if phase == Phase.SLIDE_FILL:
                    tp = state.slide_progress
                    px = int((c + (nc - c) * tp) * CELL)
                    draw_mage(surf, ci, def_side, px,
                              r * CELL + GRID_Y, 255, anim_t)
                elif phase in (Phase.SPAWN, Phase.DONE):
                    x, y = cell_xy(nr, nc)
                    draw_mage(surf, ci, def_side, x, y, 255, anim_t)
                else:
                    x, y = cell_xy(r, c)
                    draw_mage(surf, ci, side, x, y, 255, anim_t)
                continue

            # Normal cell
            x, y = cell_xy(r, c)
            draw_mage(surf, ci, side, x, y, 255, anim_t)

    # Spawn cells (fade-in of reinforcements)
    if phase in (Phase.SPAWN, Phase.DONE):
        alpha = int(state.spawn_alpha)
        for (r, c) in plan.atk_spawn_cells + plan.def_spawn_cells:
            ci = final.board[r][c]
            x, y = cell_xy(r, c)
            draw_mage(surf, ci, side_of(r, c, final), x, y, alpha, anim_t)

    # Force fields along defense formations (replaces the old outlines)
    alpha = int(state.def_outline_alpha)
    if alpha > 0:
        for df in plan.defense_formations:
            damaged = df.formation_id in state.red_def_ids
            for (r, c) in df.cells:
                if (r, c) in state.dead:
                    continue
                ci = orig.board[r][c]
                x, y = cell_xy(r, c)
                draw_field(surf, ci, def_side, x, y, field_f,
                           damaged=damaged, alpha=alpha)

    draw_frontier_dots(surf, state.vis_frontier)
    draw_grid(surf)

    if state.flash_alpha > 0:
        ov = pygame.Surface((GRID_COLS * CELL, GRID_ROWS * CELL), pygame.SRCALPHA)
        ov.fill((255, 0, 0, int(min(state.flash_alpha, 160))))
        surf.blit(ov, (0, GRID_Y))


# ---------------------------------------------------------------------------
# Selection glow — blue ground rings under units of the picked colors
# ---------------------------------------------------------------------------

GLOW_RGB = (80, 170, 255)


def draw_ground_glow(dest, x, y, size, a, phase, ground_y=0.78):
    """Additive blue ground glow + expanding ripple ring, centered near the
    bottom of a size x size box whose top-left is (x, y). Used under units
    on the grid and under the element-picker buttons."""
    s = pygame.Surface((size, size), pygame.SRCALPHA)
    cx = size // 2
    cy = int(size * ground_y)

    def scaled(k):
        return tuple(min(255, int(ch * k)) for ch in GLOW_RGB)

    # soft ground glow, growing out from the center with selection
    grow = 0.35 + 0.65 * a
    rx = int(size * 0.38 * grow)
    ry = max(3, int(rx * 0.42))
    pygame.draw.ellipse(s, scaled(0.55 * a),
                        (cx - rx, cy - ry, rx * 2, ry * 2))
    pygame.draw.ellipse(s, scaled(0.85 * a),
                        (cx - rx // 2, cy - max(2, ry // 2), rx, max(4, ry)))

    # ripple ring: expands outward and fades as it goes
    rr = int(4 + phase * size * 0.46)
    rry = max(2, int(rr * 0.42))
    ring_k = a * (1.0 - phase)
    if ring_k > 0.02 and rr > 0:
        pygame.draw.ellipse(s, scaled(1.4 * ring_k),
                            (cx - rr, cy - rry, rr * 2, rry * 2), 3)

    dest.blit(s, (x, y), special_flags=pygame.BLEND_RGB_ADD)


class SelectionGlow:
    """While a color is selected in the picker, the ground under every
    matching unit on the picking side glows: a soft ellipse that grows out
    from the center on selection, plus a repeating ripple ring that fades
    as it expands. Deselecting (or submitting) fades it back out."""

    FADE_IN  = 5.0     # selection alpha per second
    FADE_OUT = 3.0
    PERIOD   = 0.9     # ripple loop seconds
    GROUND_Y = 0.78    # ring center, as a fraction of the cell height

    def __init__(self):
        self.alpha = {}    # (is_left, color) -> 0..1
        self.t = 0.0

    def update(self, dt, selected):
        """selected: set of (is_left, color) currently picked."""
        self.t += dt
        for key in selected:
            self.alpha[key] = min(1.0, self.alpha.get(key, 0.0)
                                  + dt * self.FADE_IN)
        for key in list(self.alpha):
            if key not in selected:
                a = self.alpha[key] - dt * self.FADE_OUT
                if a <= 0:
                    del self.alpha[key]
                else:
                    self.alpha[key] = a

    def active(self):
        return bool(self.alpha)

    def draw(self, surf, bs):
        if not self.alpha:
            return
        p = (self.t / self.PERIOD) % 1.0
        for (is_left, ci), a in self.alpha.items():
            for r in range(GRID_ROWS):
                for c in range(GRID_COLS):
                    if bs.board[r][c] != ci or \
                            bs.owner_is_left(r, c) != is_left:
                        continue
                    self._cell_glow(surf, r, c, a, p)

    def _cell_glow(self, surf, r, c, a, p):
        draw_ground_glow(surf, c * CELL, r * CELL + GRID_Y, CELL, a, p,
                         ground_y=self.GROUND_Y)


# ---------------------------------------------------------------------------
# HUD / Sidebar
# ---------------------------------------------------------------------------

# who is controlled by the computer — chosen on the name-entry screen and
# locked in for the whole match. Values: None (human) or an AI_LEVELS key.
CPU_PLAYERS = {"left": None, "right": None}
PLAYER_TYPES = [None, "easy", "medium", "hard"]
TYPE_LABELS = {None: "Human", "easy": "Computer (Easy)",
               "medium": "Computer (Medium)", "hard": "Computer (Hard)"}
AI_STEP_S   = 0.55    # seconds between the AI revealing each element pick
AI_SUBMIT_S = 1.8     # seconds until the AI confirms its picks


def draw_hud(surf, fonts, atk_is_left, turn):
    font, bfont, _ = fonts
    pygame.draw.rect(surf, BG_HUD, (0, 0, SCREEN_W, HUD_H))
    title = bfont.render("MAGE GRID", True, (215, 192, 120))
    surf.blit(title, (8, 6))
    who = f"Turn {turn}  ·  {'LEFT' if atk_is_left else 'RIGHT'} attacks"
    surf.blit(font.render(who, True, (160, 160, 160)), (8, 30))


def draw_player_bars(surf, fonts, left_name, right_name, hp_left, hp_right):
    """Name + HP bar split above the grid: left half = left player, right half = right player."""
    font, bfont, _ = fonts
    grid_w = GRID_COLS * CELL
    mid_x  = (GRID_COLS // 2) * CELL   # pixel x of the grid midpoint
    ty     = HUD_H

    # Left player bar (dark background, above left territory)
    pygame.draw.rect(surf, (42, 26, 10), (0, ty, mid_x, PLAYER_BAR_H))
    name_s = bfont.render(left_name, True, (210, 185, 140))
    surf.blit(name_s, (8, ty + (PLAYER_BAR_H - name_s.get_height()) // 2))
    _player_hp_bar(surf, font, hp_left,
                   mid_x - 180, ty + (PLAYER_BAR_H - 14) // 2,
                   140, (90, 145, 255))

    # Right player bar (light background, above right territory)
    pygame.draw.rect(surf, (115, 82, 40), (mid_x, ty, grid_w - mid_x, PLAYER_BAR_H))
    name_s = bfont.render(right_name, True, (255, 240, 195))
    surf.blit(name_s, (mid_x + 8, ty + (PLAYER_BAR_H - name_s.get_height()) // 2))
    _player_hp_bar(surf, font, hp_right,
                   grid_w - 180, ty + (PLAYER_BAR_H - 14) // 2,
                   140, (255, 100, 45))


def _player_hp_bar(surf, font, hp, x, y, bw, color):
    bh = 14
    pygame.draw.rect(surf, (40, 40, 40), (x, y, bw, bh))
    fw = int(bw * max(0, hp) / HP_MAX)
    pygame.draw.rect(surf, color, (x, y, fw, bh))
    pygame.draw.rect(surf, (90, 90, 90), (x, y, bw, bh), 1)
    hp_s = font.render(str(hp), True, (220, 220, 220))
    surf.blit(hp_s, (x + bw + 6, y + 1))


def _mini_sprite(color_idx, kind, side, size):
    surf = SPRITES[color_idx][kind][side][0]
    return pygame.transform.scale(surf, (size, size))


def draw_sidebar(surf, fonts, sx, gs, pick_info=None):
    font, bfont, _ = fonts
    pygame.draw.rect(surf, BG_SIDEBAR, (sx, 0, SIDEBAR_W, SCREEN_H))

    y = HUD_H + 10

    if pick_info:
        lbl = bfont.render(
            f"{pick_info['who']}  —  {pick_info['role']}",
            True, pick_info["role_color"])
        surf.blit(lbl, (sx + 8, y)); y += lbl.get_height() + 4
        inst_txt = "Choosing elements..." if pick_info.get("cpu") \
            else "Pick 2 elements, then Enter"
        inst = font.render(inst_txt, True, (140, 140, 135))
        surf.blit(inst, (sx + 8, y)); y += inst.get_height() + 4

        if pick_info.get("confirm_msg") and pick_info.get("confirm_timer", 0) > 0:
            cm = font.render(pick_info["confirm_msg"], True, (150, 240, 125))
            surf.blit(cm, (sx + 8, y))
        y += 20

        pygame.draw.line(surf, (55, 50, 44),
                         (sx + 8, y), (sx + SIDEBAR_W - 8, y), 1)
        y += 10

    legend_side = "left"
    if pick_info and not pick_info.get("picking_left", True):
        legend_side = "right"
    surf.blit(bfont.render("Elements", True, (200, 178, 110)), (sx + 8, y)); y += 22
    for i in range(NUM_COLORS):
        surf.blit(_mini_sprite(i, "idle", legend_side, 20), (sx + 10, y - 2))
        surf.blit(font.render(f"[{ELEM_KEYS[i]}] {ELEM_NAMES[i]}",
                              True, (195, 195, 195)), (sx + 34, y))
        y += 21
    y += 9

    if "atk_colors" in gs:
        surf.blit(font.render("Attack:", True, (255, 155, 70)), (sx + 8, y))
        for i, ci in enumerate(gs["atk_colors"]):
            surf.blit(_mini_sprite(ci, "ball", "left", 18), (sx + 70 + i * 24, y))
        y += 24
    if "def_colors" in gs:
        surf.blit(font.render("Defend:", True, (70, 185, 255)), (sx + 8, y))
        for i, ci in enumerate(gs["def_colors"]):
            surf.blit(_mini_sprite(ci, "fld", "left", 18), (sx + 70 + i * 24, y))
        y += 24

    fy = SCREEN_H - 108
    pygame.draw.line(surf, (55, 50, 44),
                     (sx + 8, fy), (sx + SIDEBAR_W - 8, fy), 1)
    fy += 8
    for line in ["W F L N R = elements", "Enter to confirm",
                 "Click to select", "M = sound on/off",
                 "ESC = pause / quit"]:
        surf.blit(font.render(line, True, (105, 103, 96)), (sx + 8, fy))
        fy += 18


# ---------------------------------------------------------------------------
# Element picker widget — each button shows the picking side's unit sprite
# standing on its own territory, with the same blue ground glow the units
# get on the grid fading in and out while the element is selected.
# ---------------------------------------------------------------------------

class ElementPicker:
    SZ = 40                       # button size (sprite drawn at 36px inside)
    SPR = 36

    def __init__(self, sx):
        self.sx  = sx
        self.sel = []
        self.t   = 0.0
        self.glow_a = {}          # element -> 0..1 selection fade
        y0 = HUD_H + 222
        self.btns = [(pygame.Rect(sx+8+i*(self.SZ+4), y0, self.SZ, self.SZ), i)
                     for i in range(NUM_COLORS)]

    def reset(self):  self.sel = []
    def ready(self):  return len(self.sel) == 2
    def get(self):    return tuple(self.sel)

    def update(self, dt):
        self.t += dt
        for ci in range(NUM_COLORS):
            a = self.glow_a.get(ci, 0.0)
            if ci in self.sel:
                a = min(1.0, a + dt * SelectionGlow.FADE_IN)
            else:
                a = max(0.0, a - dt * SelectionGlow.FADE_OUT)
            self.glow_a[ci] = a

    def _toggle(self, ci):
        if ci in self.sel:
            self.sel.remove(ci)
            play_sfx("deselect")
        elif len(self.sel) < 2:
            self.sel.append(ci)
            play_sfx("select")

    def handle_click(self, pos):
        for rect, ci in self.btns:
            if rect.collidepoint(pos):
                self._toggle(ci)
                return True
        return False

    def handle_key(self, key):
        km = {pygame.K_w: 0, pygame.K_f: 1, pygame.K_l: 2,
              pygame.K_n: 3, pygame.K_r: 4}
        ci = km.get(key)
        if ci is not None:
            self._toggle(ci)

    def draw(self, surf, font, side="left", anim_t=0.0):
        phase = (self.t / SelectionGlow.PERIOD) % 1.0
        bg = BG_LEFT if side == "left" else BG_RIGHT
        pad = (self.SZ - self.SPR) // 2
        for rect, ci in self.btns:
            pygame.draw.rect(surf, bg, rect, border_radius=6)
            a = self.glow_a.get(ci, 0.0)
            if a > 0:
                draw_ground_glow(surf, rect.x, rect.y, self.SZ, a, phase)
            sprite = SPRITES[ci]["idle"][side][idle_frame(anim_t)]
            sprite = pygame.transform.scale(sprite, (self.SPR, self.SPR))
            surf.blit(sprite, (rect.x + pad, rect.y + pad))
            key_s = font.render(ELEM_KEYS[ci], True, (235, 225, 200))
            surf.blit(font.render(ELEM_KEYS[ci], True, (20, 15, 10)),
                      (rect.x + 4, rect.y + 1))
            surf.blit(key_s, (rect.x + 3, rect.y))
            pygame.draw.rect(surf, (90, 80, 66), rect, 1, border_radius=6)
        y = self.btns[0][0].bottom + 8
        if self.sel:
            txt = " + ".join(ELEM_NAMES[ci] for ci in self.sel)
        else:
            txt = "Pick 2 elements"
        surf.blit(font.render(txt, True, (195,195,175)), (self.sx+8, y))


# ---------------------------------------------------------------------------
# Music (generated offline by gen_music.py -> assets/*.wav)
#   prelude — title + name screens (mustering for battle)
#   battle  — the match itself
#   victory — from the killing blow through the game-over screen
# ---------------------------------------------------------------------------

MUSIC_VOLUME = 0.6

# --- sound effects (generated offline by gen_sfx.py -> assets/sfx_*.wav) ---
SFX = {}
SFX_ENABLED = True
KILL_SFX = ("kill_water", "kill_fire", "kill_lightning",
            "kill_nature", "kill_rock")          # indexed by element/color
SFX_VOLS = {
    "kill_water": 0.5, "kill_fire": 0.5, "kill_lightning": 0.5,
    "kill_nature": 0.5, "kill_rock": 0.5,
    "sacrifice": 0.3, "block": 0.55, "breakthrough": 0.68,
    "transform": 0.38, "field": 0.38, "poof": 0.3,
    "select": 0.3, "deselect": 0.26, "confirm": 0.38,
}


def load_sfx():
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
    for name, vol in SFX_VOLS.items():
        try:
            snd = pygame.mixer.Sound(os.path.join(base, f"sfx_{name}.wav"))
            snd.set_volume(vol)
            SFX[name] = snd
        except (pygame.error, FileNotFoundError):
            pass                # missing file / no audio: that effect is silent


def play_sfx(name):
    if not SFX_ENABLED:
        return
    snd = SFX.get(name)
    if snd is not None:
        try:
            snd.play()
        except pygame.error:
            pass


def start_music_track(name, music_on=True):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "assets", f"{name}.wav")
    try:
        pygame.mixer.music.load(path)
        pygame.mixer.music.set_volume(MUSIC_VOLUME if music_on else 0.0)
        pygame.mixer.music.play(-1)
    except pygame.error:
        pass                    # no audio device / missing file: stay silent


def set_music_on(music_on):
    """The M toggle governs all sound: music volume and effects."""
    global SFX_ENABLED
    SFX_ENABLED = music_on
    try:
        pygame.mixer.music.set_volume(MUSIC_VOLUME if music_on else 0.0)
    except pygame.error:
        pass


# ---------------------------------------------------------------------------
# Tutorial
# ---------------------------------------------------------------------------
# A scripted match vs the "Tutor" (2 HP). Each lesson RESETS the board to a
# fixed layout, so the setup is identical every run: a checkerboard of
# Lightning/Rock (which can never form a run for any other element pair)
# with the lesson's formation cells painted on top. The player is told what
# to pick, the relevant cells pulse, and the Tutor's picks are scripted.
# After the last lesson the match continues as a normal game vs the AI.
# ---------------------------------------------------------------------------

TUTORIAL_STEPS = [
    {   # 1 — attack basics: one 3-unit formation, undefended
        "attacker_left": True,
        "player_pick": (0, 1), "cpu_pick": (0, 3),
        "cells": [(4, 2, 0), (4, 3, 1), (4, 4, 0)],
        "highlight": [(4, 2), (4, 3), (4, 4)],
        "lines": ["ATTACK BASICS",
                  "Pick 2 elements. Any 3+",
                  "of them side by side in",
                  "a row of your land form",
                  "an attack formation.",
                  "",
                  "Pick WATER + FIRE and",
                  "press Enter. The marked",
                  "three will charge: the",
                  "front two fall crossing",
                  "the border, each soldier",
                  "behind kills 1 defender."],
    },
    {   # 2 — defense basics: block a known attack
        "attacker_left": False,
        "player_pick": (1, 3), "cpu_pick": (0, 1),
        "cells": [(2, 4, 3), (3, 4, 1), (4, 4, 3),
                  (3, 5, 1), (3, 6, 0), (3, 7, 1)],
        "highlight": [(2, 4), (3, 4), (4, 4)],
        "lines": ["DEFENSE BASICS",
                  "The Tutor attacks with",
                  "WATER + FIRE: its squad",
                  "in row 3 will charge",
                  "your column 4.",
                  "",
                  "3+ of your elements",
                  "stacked in a COLUMN",
                  "raise a force-field",
                  "wall. Pick FIRE +",
                  "NATURE to wall off the",
                  "marked cells."],
    },
    {   # 3 — two attack formations at once
        "attacker_left": True,
        "player_pick": (0, 3), "cpu_pick": (1, 2),
        "cells": [(2, 2, 0), (2, 3, 3), (2, 4, 0),
                  (6, 2, 3), (6, 3, 0), (6, 4, 3)],
        "highlight": [(2, 2), (2, 3), (2, 4), (6, 2), (6, 3), (6, 4)],
        "lines": ["MULTIPLE FORMATIONS",
                  "One pick can launch",
                  "several squads at once.",
                  "",
                  "Pick WATER + NATURE:",
                  "the squads in rows 2",
                  "and 6 both charge, top",
                  "row first."],
    },
    {   # 4 — one tall wall blocks two attacks
        "attacker_left": False,
        "player_pick": (0, 1), "cpu_pick": (0, 3),
        "cells": [(1, 4, 0), (2, 4, 1), (3, 4, 0), (4, 4, 1), (5, 4, 0),
                  (1, 5, 0), (1, 6, 3), (1, 7, 0),
                  (5, 5, 3), (5, 6, 0), (5, 7, 3)],
        "highlight": [(1, 4), (2, 4), (3, 4), (4, 4), (5, 4)],
        "lines": ["BIG WALLS",
                  "Two enemy squads are",
                  "coming, in rows 1 and 5.",
                  "One tall wall can block",
                  "them both.",
                  "",
                  "Pick WATER + FIRE to",
                  "raise a 5-unit wall in",
                  "column 4. It weakens on",
                  "the first hit and",
                  "crumbles when the",
                  "attack ends."],
    },
    {   # 5 — flanking cascade
        "attacker_left": True,
        "player_pick": (1, 3), "cpu_pick": (0, 2),
        "cells": [(4, 0, 1), (4, 1, 3), (4, 2, 1), (4, 3, 3), (4, 4, 1)],
        "highlight": [(4, 0), (4, 1), (4, 2), (4, 3), (4, 4)],
        "lines": ["FLANKING",
                  "Bigger squads cut deeper.",
                  "Pick FIRE + NATURE: five",
                  "soldiers make 3 kills",
                  "ahead - and the cascade",
                  "also cuts down defenders",
                  "above and below the",
                  "earlier kills. Survivors",
                  "fall back to plug the",
                  "holes, and you claim the",
                  "freed ground after the",
                  "battle."],
    },
    {   # 6 — an early block stops the whole cascade
        "attacker_left": False,
        "player_pick": (0, 4), "cpu_pick": (1, 3),
        "cells": [(3, 4, 0), (4, 4, 4), (5, 4, 0),
                  (4, 5, 1), (4, 6, 3), (4, 7, 1), (4, 8, 3), (4, 9, 1)],
        "highlight": [(3, 4), (4, 4), (5, 4)],
        "lines": ["STOP THE CASCADE",
                  "A 5-soldier squad is",
                  "charging along row 4!",
                  "Flanking only grows",
                  "while a squad advances:",
                  "block its FIRST step and",
                  "nothing else happens.",
                  "",
                  "Pick WATER + ROCK for a",
                  "wall in column 4."],
    },
    {   # 7 — breakthrough: hit the back line
        "attacker_left": True,
        "player_pick": (0, 1), "cpu_pick": (0, 3),
        "frontier": [4, 4, 4, 4, 8, 4, 4, 4, 4],
        "cells": [(4, 5, 0), (4, 6, 1), (4, 7, 0), (4, 8, 1)],
        "highlight": [(4, 5), (4, 6), (4, 7), (4, 8)],
        "lines": ["BREAKTHROUGH",
                  "Reach the enemy's back",
                  "line and they lose 1 HP.",
                  "Your squad in row 4 is",
                  "already deep in their",
                  "land: pick WATER + FIRE,",
                  "kill the last defender",
                  "and charge off the board.",
                  "The enemy claws back a",
                  "wedge of ground after.",
                  "The Tutor has 2 HP -",
                  "one more hit wins!"],
    },
]

TUTORIAL_FREE_LINES = [
    "TUTORIAL COMPLETE",
    "Finish the match! The",
    "Tutor plays for real",
    "now, with 1 HP left.",
    "",
    "One last rule: in real",
    "matches the first",
    "defender starts with 2",
    "bonus tiles as a",
    "handicap. Good luck!",
]


def make_tutorial_board(step, hp_left, hp_right):
    board = [[2 if (r + c) % 2 == 0 else 4 for c in range(GRID_COLS)]
             for r in range(GRID_ROWS)]
    for (r, c, ci) in step["cells"]:
        board[r][c] = ci
    frontier = step.get("frontier", [STARTING_FRONTIER] * GRID_ROWS)
    return BoardState(board=board, frontier=frontier[:],
                      hp_left=hp_left, hp_right=hp_right)


def draw_tut_panel(surf, fonts, lines):
    font, bfont, _ = fonts
    sx = GRID_COLS * CELL
    y = HUD_H + 352
    pygame.draw.rect(surf, BG_SIDEBAR,
                     (sx, y - 8, SIDEBAR_W, SCREEN_H - y + 8))
    pygame.draw.line(surf, (55, 50, 44),
                     (sx + 8, y - 8), (sx + SIDEBAR_W - 8, y - 8), 1)
    for i, line in enumerate(lines):
        if i == 0:
            surf.blit(bfont.render(line, True, (255, 216, 120)), (sx + 8, y))
            y += 24
        else:
            surf.blit(font.render(line, True, (200, 196, 184)), (sx + 8, y))
            y += 18


def draw_tut_highlights(surf, cells, t):
    a = int(140 + 90 * math.sin(t * 4.5))
    for (r, c) in cells:
        s = pygame.Surface((CELL, CELL), pygame.SRCALPHA)
        pygame.draw.rect(s, (255, 220, 90, a), (2, 2, CELL - 4, CELL - 4), 3)
        surf.blit(s, (c * CELL, r * CELL + GRID_Y))


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
    for i in range(40):
        random.seed(i * 131 + 7)
        x = random.randint(0, SCREEN_W)
        y = random.randint(0, SCREEN_H)
        a = int(100 + 100 * math.sin(t*1.3 + i*0.7))
        s = pygame.Surface((3,3), pygame.SRCALPHA)
        pygame.draw.circle(s, (210,195,140,a), (1,1), 1)
        surf.blit(s, (x,y))
    random.seed()
    title = tfont.render("MAGE GRID", True, (218, 192, 100))
    surf.blit(title, (SCREEN_W//2 - title.get_width()//2, SCREEN_H//3 - 50))
    sub = bfont.render("A tactical grid battle", True, (150,140,120))
    surf.blit(sub, (SCREEN_W//2 - sub.get_width()//2, SCREEN_H//3 + 38))

    # the five mages idle across the title screen
    seq_f = IDLE_SEQ[int(t / IDLE_FRAME_S) % len(IDLE_SEQ)]
    total_w = NUM_COLORS * SPRITE_PX + (NUM_COLORS - 1) * 18
    x0 = SCREEN_W // 2 - total_w // 2
    for i in range(NUM_COLORS):
        surf.blit(SPRITES[i]["idle"]["left"][seq_f],
                  (x0 + i * (SPRITE_PX + 18), SCREEN_H // 2 - 20))

    pulse = int(190 + 60 * math.sin(t * 2.6))
    go = bfont.render("Press ENTER to begin", True, (pulse, pulse, pulse))
    surf.blit(go, (SCREEN_W//2 - go.get_width()//2, SCREEN_H//2 + 60))
    esc = pygame.font.SysFont("segoeui", 16).render(
        "ESC = quit  ·  M = sound on/off", True, (120, 115, 105))
    surf.blit(esc, (SCREEN_W//2 - esc.get_width()//2, SCREEN_H - 44))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    pygame.init()
    # The game renders to a fixed logical canvas (`screen`), which is then
    # scaled each frame to the largest aspect-preserving fit of the
    # resizable window — any scale factor, bars on at most one axis — and
    # mouse coordinates are mapped back through the same transform.
    # open at 1.5x by default (clamped so it always fits the display)
    info = pygame.display.Info()
    win_scale = 1.5
    if info.current_w > 0 and info.current_h > 0:
        win_scale = min(win_scale,
                        info.current_w * 0.94 / SCREEN_W,
                        info.current_h * 0.90 / SCREEN_H)
        win_scale = max(win_scale, 0.5)
    window = pygame.display.set_mode((int(SCREEN_W * win_scale),
                                      int(SCREEN_H * win_scale)),
                                     pygame.RESIZABLE)
    screen = pygame.Surface((SCREEN_W, SCREEN_H)).convert()
    pygame.display.set_caption("Mage Grid — Sprite Edition")
    clock = pygame.time.Clock()
    load_sprites()

    font   = pygame.font.SysFont("segoeui",    16)
    bfont  = pygame.font.SysFont("segoeui",    18, bold=True)
    tfont  = pygame.font.SysFont("segoeuiblack", 50, bold=True)
    fonts  = (font, bfont, tfont)
    sx     = GRID_COLS * CELL

    phase        = "title"
    t            = 0.0
    left_name    = "Left"
    right_name   = "Right"
    bs: BoardState = make_board()
    turn         = 1
    atk_is_left  = True
    anim: Optional[AnimState] = None

    name_step = 0
    inputs    = [TextInput("Left player name:"), TextInput("Right player name:")]
    # per-player type (Human / Computer difficulty), cycled on the name
    # screen and locked in for the whole match
    ptype     = [CPU_PLAYERS["left"], CPU_PLAYERS["right"]]
    type_btns = [pygame.Rect(SCREEN_W // 2 + 190, 150 + i * 90 + 14, 170, 30)
                 for i in range(2)]

    def cycle_type(i):
        ptype[i] = PLAYER_TYPES[(PLAYER_TYPES.index(ptype[i]) + 1)
                                % len(PLAYER_TYPES)]

    picker     = ElementPicker(sx)
    glow       = SelectionGlow()
    color_step = 0
    atk_colors = None
    def_colors = None
    confirm_msg   = ""
    confirm_timer = 0.0
    gs            = {"phase": "title"}
    ai_choice     = None      # elements the computer decided on this pick
    ai_timer      = 0.0
    tut           = None      # None | {"step": i} | {"free": True}
    tut_btn       = pygame.Rect(SCREEN_W // 2 - 75, 430, 150, 34)
    music_on      = True
    cur_track     = None
    paused        = None      # None | "match" (quit to title) | "app" (quit game)
    load_ai_weights()
    load_sfx()

    def play_track(name):
        nonlocal cur_track
        if cur_track != name:
            cur_track = name
            start_music_track(name, music_on)

    play_track("prelude")

    def do_submit():
        """Confirm the picker's two elements (Enter key or computer turn)."""
        nonlocal color_step, atk_colors, def_colors, confirm_msg
        nonlocal confirm_timer, anim, phase, gs, ai_choice, ai_timer
        play_sfx("confirm")
        if color_step == 0:
            atk_colors = picker.get()
            color_step = 1
            picker.reset()
            # never reveal the attack elements to the defender
            confirm_msg = "Attack locked in!"
            confirm_timer = 1.8
        else:
            def_colors = picker.get()
            plan = build_combat_plan(bs, atk_is_left, atk_colors, def_colors)
            anim = AnimState(plan, bs)
            phase = "anim"
            gs = {"phase": "anim",
                  "atk_colors": atk_colors,
                  "def_colors": def_colors}
            if plan.defense_formations:
                play_sfx("field")          # the walls rise
        ai_choice = None
        ai_timer = 0.0

    def load_tut_step(hp_l, hp_r):
        """Reset the board to the current lesson's fixed layout."""
        nonlocal bs, atk_is_left, phase, color_step, atk_colors, def_colors
        nonlocal gs, ai_choice, ai_timer
        step = TUTORIAL_STEPS[tut["step"]]
        bs = make_tutorial_board(step, hp_l, hp_r)
        atk_is_left = step["attacker_left"]
        phase = "pick"; color_step = 0; picker.reset()
        atk_colors = None; def_colors = None
        ai_choice = None; ai_timer = 0.0
        gs = {"phase": "pick"}

    while True:
        dt = clock.tick(FPS) / 1000.0
        if paused:
            dt = 0.0             # the whole game world freezes
        t += dt
        confirm_timer = max(0.0, confirm_timer - dt)

        # window -> logical canvas transform for this frame
        win_w, win_h = window.get_size()
        view_scale = min(win_w / SCREEN_W, win_h / SCREEN_H)
        view_w = max(1, int(SCREEN_W * view_scale))
        view_h = max(1, int(SCREEN_H * view_scale))
        view_x = (win_w - view_w) // 2
        view_y = (win_h - view_h) // 2

        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                pygame.quit(); sys.exit()

            # map window mouse coordinates back into the logical canvas
            if ev.type == pygame.MOUSEBUTTONDOWN and view_scale > 0:
                mx, my = ev.pos
                lp = (int((mx - view_x) / view_scale),
                      int((my - view_y) / view_scale))
                ev = pygame.event.Event(ev.type, {**ev.dict, "pos": lp})

            # music toggle (everywhere except while typing a name)
            if ev.type == pygame.KEYDOWN and ev.key == pygame.K_m and \
                    phase != "name_entry":
                music_on = not music_on
                set_music_on(music_on)

            # paused: only the quit prompt listens
            if paused:
                if ev.type == pygame.KEYDOWN:
                    if ev.key == pygame.K_y:
                        if paused == "app":
                            pygame.quit(); sys.exit()
                        paused = None
                        phase = "title"; gs = {"phase": "title"}
                        anim = None; tut = None
                        picker.reset(); color_step = 0
                        atk_colors = None; def_colors = None
                        ai_choice = None; ai_timer = 0.0
                        play_track("prelude")
                    elif ev.key in (pygame.K_n, pygame.K_ESCAPE):
                        paused = None
                continue

            # ESC: during a match, offer quit-to-title; on the title and
            # name screens, offer quitting the game entirely
            if ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE:
                if phase in ("pick", "anim"):
                    paused = "match"
                    continue
                if phase in ("title", "name_entry"):
                    paused = "app"
                    continue

            if phase == "title":
                if ev.type == pygame.KEYDOWN and ev.key == pygame.K_RETURN:
                    phase = "name_entry"
                    name_step = 0
                    inputs[0].text = ""; inputs[1].text = ""

            elif phase == "name_entry":
                if ev.type == pygame.MOUSEBUTTONDOWN:
                    for i, rect in enumerate(type_btns):
                        if rect.collidepoint(ev.pos):
                            cycle_type(i)
                    if tut_btn.collidepoint(ev.pos):
                        tut = {"step": 0}
                        CPU_PLAYERS["left"] = None
                        CPU_PLAYERS["right"] = "medium"
                        left_name, right_name = "You", "Tutor (CPU)"
                        turn = 1
                        load_tut_step(HP_MAX, 2)
                        play_track("battle")
                if ev.type == pygame.KEYDOWN:
                    if ev.key == pygame.K_TAB:
                        cycle_type(name_step)
                    elif inputs[name_step].handle(ev):
                        name_step += 1
                        if name_step >= 2:
                            # lock in who is computer-controlled for the match
                            CPU_PLAYERS["left"]  = ptype[0]
                            CPU_PLAYERS["right"] = ptype[1]

                            def _pname(i):
                                txt = inputs[i].text.strip()
                                if ptype[i]:
                                    tag = ptype[i].title()
                                    return (txt + f" ({tag} CPU)") if txt \
                                        else f"CPU ({tag})"
                                return txt or "Player"
                            left_name  = _pname(0)
                            right_name = _pname(1)
                            # first defender gets the two-tile head start
                            bs = make_board(first_attacker_is_left=atk_is_left)
                            phase = "pick"; color_step = 0; picker.reset()
                            gs = {"phase": "pick"}
                            play_track("battle")

            elif phase == "pick":
                picking_left = atk_is_left if color_step == 0 \
                    else (not atk_is_left)
                cpu_turn = CPU_PLAYERS["left" if picking_left else "right"]
                if not cpu_turn:
                    if ev.type == pygame.KEYDOWN:
                        if ev.key == pygame.K_RETURN and picker.ready():
                            # tutorial lessons require the taught pick
                            if tut and not tut.get("free") and \
                                    tuple(sorted(picker.get())) != tuple(sorted(
                                        TUTORIAL_STEPS[tut["step"]]["player_pick"])):
                                a, b = TUTORIAL_STEPS[tut["step"]]["player_pick"]
                                confirm_msg = (f"Lesson: pick {ELEM_NAMES[a]}"
                                               f" + {ELEM_NAMES[b]}")
                                confirm_timer = 2.2
                            else:
                                do_submit()
                        else:
                            picker.handle_key(ev.key)
                    if ev.type == pygame.MOUSEBUTTONDOWN:
                        picker.handle_click(ev.pos)

            elif phase == "gameover":
                if ev.type == pygame.KEYDOWN and ev.key == pygame.K_RETURN:
                    phase = "title"
                    tut = None
                    play_track("prelude")

        if phase == "name_entry":
            inputs[name_step].update(dt)

        # computer player turn
        if phase == "pick":
            picking_left = atk_is_left if color_step == 0 else (not atk_is_left)
            if CPU_PLAYERS["left" if picking_left else "right"]:
                if ai_choice is None:
                    level = CPU_PLAYERS["left" if picking_left else "right"]
                    if tut and not tut.get("free"):
                        # the Tutor's picks are scripted by the lesson
                        ai_choice = TUTORIAL_STEPS[tut["step"]]["cpu_pick"]
                    elif color_step == 0:
                        ai_choice = ai_choose_attack(bs, atk_is_left,
                                                     level=level)
                    else:
                        ai_choice = ai_choose_defense(bs, atk_is_left,
                                                      level=level)
                    picker.reset()
                    ai_timer = 0.0
                ai_timer += dt
                if color_step == 0:
                    # attack picks must stay secret from the defender: no
                    # staged reveal, no grid glow — think, then submit
                    if ai_timer >= AI_SUBMIT_S * 0.7:
                        picker.sel = list(ai_choice)
                        do_submit()
                else:
                    # defense picks are revealed one by one with the glow
                    want = min(2, int(ai_timer / AI_STEP_S))
                    while len(picker.sel) < want:
                        picker.sel.append(ai_choice[len(picker.sel)])
                    if ai_timer >= AI_SUBMIT_S and picker.ready():
                        do_submit()

        if phase == "anim" and anim is not None:
            # the killing blow cues the victory theme immediately
            if anim.match_over_at is not None:
                play_track("victory")
            if advance_anim(anim, dt):
                bs          = anim.plan.final_board
                turn       += 1
                atk_is_left = not atk_is_left
                anim        = None
                if bs.hp_left <= 0 or bs.hp_right <= 0:
                    phase = "gameover"; gs = {"phase": "gameover"}
                elif tut and not tut.get("free"):
                    tut["step"] += 1
                    if tut["step"] >= len(TUTORIAL_STEPS):
                        # lessons done: play out the match for real
                        tut = {"free": True}
                        phase = "pick"; color_step = 0; picker.reset()
                        atk_colors = None; def_colors = None
                        gs = {"phase": "pick"}
                    else:
                        load_tut_step(bs.hp_left, bs.hp_right)
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
                rect = type_btns[i]
                is_cpu = ptype[i] is not None
                pygame.draw.rect(screen, (34, 46, 62) if is_cpu else (36, 30, 22),
                                 rect, border_radius=6)
                bc = (120, 190, 255) if is_cpu else (110, 100, 85)
                pygame.draw.rect(screen, bc, rect,
                                 2 if i == name_step else 1, border_radius=6)
                lbl = font.render(TYPE_LABELS[ptype[i]], True,
                                  (150, 205, 255) if is_cpu else (200, 195, 185))
                screen.blit(lbl, (rect.x + (rect.w - lbl.get_width()) // 2,
                                  rect.y + (rect.h - lbl.get_height()) // 2))
            hint = font.render(
                "Click the button (or press Tab) to cycle Human / Computer "
                "difficulty — locked in for the match", True, (130, 125, 115))
            screen.blit(hint, (SCREEN_W//2 - hint.get_width()//2, 360))

            pygame.draw.rect(screen, (34, 46, 40), tut_btn, border_radius=6)
            pygame.draw.rect(screen, (110, 200, 140), tut_btn, 1,
                             border_radius=6)
            tl = bfont.render("Tutorial", True, (150, 230, 170))
            screen.blit(tl, (tut_btn.x + (tut_btn.w - tl.get_width()) // 2,
                             tut_btn.y + (tut_btn.h - tl.get_height()) // 2))
            tsub = font.render("New to the game? Learn it one turn at a time.",
                               True, (110, 125, 112))
            screen.blit(tsub, (SCREEN_W//2 - tsub.get_width()//2,
                               tut_btn.bottom + 8))
            esc_hint = font.render("ESC = quit", True, (120, 115, 105))
            screen.blit(esc_hint, (SCREEN_W//2 - esc_hint.get_width()//2,
                                   tut_btn.bottom + 34))

        elif phase == "pick":
            picking_left = atk_is_left if color_step == 0 else (not atk_is_left)
            picker.update(dt)
            glow.update(dt, {(picking_left, ci) for ci in picker.sel})
            draw_static(screen, bs, active_left=picking_left, anim_t=t,
                        glow=glow)
            draw_hud(screen, fonts, atk_is_left, turn)
            draw_player_bars(screen, fonts, left_name, right_name,
                             bs.hp_left, bs.hp_right)
            atker = left_name  if atk_is_left else right_name
            defer = right_name if atk_is_left else left_name
            who   = atker if color_step == 0 else defer
            role  = "ATTACKING" if color_step == 0 else "DEFENDING"
            rc    = (255, 155, 55) if color_step == 0 else (65, 175, 255)
            pick_info = {
                "who": who, "role": role, "role_color": rc,
                "picking_left": picking_left,
                "cpu": CPU_PLAYERS["left" if picking_left else "right"],
                "confirm_msg": confirm_msg, "confirm_timer": confirm_timer,
            }
            draw_sidebar(screen, fonts, sx, gs, pick_info)
            picker.draw(screen, font,
                        side="left" if picking_left else "right", anim_t=t)
            if tut:
                if tut.get("free"):
                    draw_tut_panel(screen, fonts, TUTORIAL_FREE_LINES)
                else:
                    step = TUTORIAL_STEPS[tut["step"]]
                    draw_tut_highlights(screen, step["highlight"], t)
                    draw_tut_panel(screen, fonts, step["lines"])

        elif phase == "anim" and anim is not None:
            draw_animated(screen, anim, anim_t=t)
            # the defender's last selection rings fade out as combat begins
            glow.update(dt, set())
            if glow.active():
                glow.draw(screen, anim.orig)
            draw_hud(screen, fonts, atk_is_left, turn)
            draw_player_bars(screen, fonts, left_name, right_name,
                             anim.hp_left, anim.hp_right)
            draw_sidebar(screen, fonts, sx, gs)
            if tut:
                lines = TUTORIAL_FREE_LINES if tut.get("free") \
                    else TUTORIAL_STEPS[tut["step"]]["lines"]
                draw_tut_panel(screen, fonts, lines)

        elif phase == "gameover":
            # the winning army keeps celebrating behind the overlay
            draw_static(screen, bs, celebrate_t=t)
            draw_hud(screen, fonts, atk_is_left, turn)
            draw_player_bars(screen, fonts, left_name, right_name,
                             bs.hp_left, bs.hp_right)
            ov = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)
            ov.fill((0,0,0,155)); screen.blit(ov, (0,0))
            cx = SCREEN_W // 2
            if bs.hp_left <= 0 and bs.hp_right <= 0:
                msg = "DRAW!"
            elif bs.hp_left <= 0:
                msg = f"{right_name} WINS!"
            else:
                msg = f"{left_name} WINS!"
            wl = tfont.render(msg, True, (255, 218, 70))
            screen.blit(wl, (cx - wl.get_width()//2, SCREEN_H//3))
            sub = font.render("Press ENTER to return to title", True, (175,175,175))
            screen.blit(sub, (cx - sub.get_width()//2, SCREEN_H//2 + 20))

        # pause overlay: the frozen game stays visible underneath
        if paused:
            ov = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)
            ov.fill((0, 0, 0, 140))
            screen.blit(ov, (0, 0))
            box = pygame.Rect(0, 0, 380, 150)
            box.center = (SCREEN_W // 2, SCREEN_H // 2)
            pygame.draw.rect(screen, (28, 22, 16), box, border_radius=10)
            pygame.draw.rect(screen, (215, 192, 120), box, 2, border_radius=10)
            hdr = bfont.render("PAUSED", True, (255, 216, 120))
            screen.blit(hdr, (box.centerx - hdr.get_width() // 2, box.y + 18))
            q_txt = "Quit the game?" if paused == "app" \
                else "Quit to the title screen?"
            q = font.render(q_txt, True, (210, 205, 195))
            screen.blit(q, (box.centerx - q.get_width() // 2, box.y + 58))
            yn = font.render("Y = quit        N = resume", True, (160, 155, 145))
            screen.blit(yn, (box.centerx - yn.get_width() // 2, box.y + 96))

        # present the logical canvas scaled to the window
        if (win_w, win_h) == (SCREEN_W, SCREEN_H):
            window.blit(screen, (0, 0))
        else:
            window.fill((0, 0, 0))
            frame = pygame.transform.smoothscale(screen, (view_w, view_h))
            window.blit(frame, (view_x, view_y))
        pygame.display.flip()


if __name__ == "__main__":
    main()
