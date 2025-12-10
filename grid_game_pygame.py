import pygame
import random
import sys
import time

# ---------- GAME CONSTANTS ----------

WIDTH = 9
HEIGHT = 10

CELL_SIZE = 64

STARTING_HP = 5

# Side and top/bottom layout
LEFT_MARGIN = 180   # space for left HUD
RIGHT_MARGIN = 180  # space for right HUD
TOP_UI = 90
BOTTOM_UI = 180  # space for buttons + text under grid

SCREEN_WIDTH = WIDTH * CELL_SIZE + LEFT_MARGIN + RIGHT_MARGIN
SCREEN_HEIGHT = TOP_UI + HEIGHT * CELL_SIZE + BOTTOM_UI

# Colors (RGB)
DARK_BROWN = (80, 50, 20)
LIGHT_BROWN = (170, 130, 80)
WHITE = (240, 240, 240)
BLACK = (10, 10, 10)
GRAY = (120, 120, 120)
RED = (220, 80, 80)

COLOR_DEFS = [
    {"name": "Blue",   "letter": "B", "rgb": (80, 120, 255)},
    {"name": "Red",    "letter": "R", "rgb": (230, 80, 80)},
    {"name": "Yellow", "letter": "Y", "rgb": (250, 220, 80)},
    {"name": "Green",  "letter": "G", "rgb": (80, 180, 80)},
    {"name": "White",  "letter": "W", "rgb": (240, 240, 240)},
]

NUM_COLORS = len(COLOR_DEFS)

# Animation timings (seconds)
FADE_OUT_DURATION = 1.0          # non-attacking attackers fade out
FORMATION_MOVE_PER = 0.7         # per-formation move duration
FORMATION_FADE_PER = 0.5         # per-formation fade duration (after it reaches final square)
SLIDE_DURATION = FADE_OUT_DURATION * 3.0  # 3 stages: fade-in, slide, spawn-fade-in

FPS = 60
NAME_MAX_LEN = 15


# ---------- CORE GAME LOGIC ----------

def random_color_index():
    return random.randrange(NUM_COLORS)


def create_board():
    board = [[random_color_index() for _ in range(WIDTH)] for _ in range(HEIGHT)]
    frontier = [HEIGHT // 2 - 1 for _ in range(WIDTH)]  # rows 0..4 = top, 5..9 = bottom
    return board, frontier


def owner_is_top(r, c, frontier):
    return r <= frontier[c]


def get_attack_groups(board, frontier, attacker_is_top, chosen_colors):
    groups = []
    for c in range(WIDTH):
        if attacker_is_top:
            low = 0
            high = frontier[c]
        else:
            low = frontier[c] + 1
            high = HEIGHT - 1
        if low > high:
            continue

        run_rows = []
        for r in range(low, high + 1):
            color_idx = board[r][c]
            if color_idx in chosen_colors:
                if not run_rows or r == run_rows[-1] + 1:
                    run_rows.append(r)
                else:
                    if len(run_rows) >= 3:
                        groups.append({"col": c, "rows": run_rows[:]})
                    run_rows = [r]
            else:
                if len(run_rows) >= 3:
                    groups.append({"col": c, "rows": run_rows[:]})
                run_rows = []
        if len(run_rows) >= 3:
            groups.append({"col": c, "rows": run_rows[:]})
    return groups


def get_barrier_cells(board, frontier, defender_is_top, chosen_colors):
    """
    Return a set of (row, col) cells that are part of any horizontal barrier
    (i.e., contiguous runs of >=3 of chosen_colors on the defender's side).
    """
    barrier_cells = set()
    for r in range(HEIGHT):
        run_cols = []
        for c in range(WIDTH):
            is_defender_cell = (owner_is_top(r, c, frontier) == defender_is_top)
            if is_defender_cell and board[r][c] in chosen_colors:
                run_cols.append(c)
            else:
                if len(run_cols) >= 3:
                    for cc in run_cols:
                        barrier_cells.add((r, cc))
                run_cols = []
        if len(run_cols) >= 3:
            for cc in run_cols:
                barrier_cells.add((r, cc))
    return barrier_cells


def resolve_combat(board, frontier, attacker_is_top, atk_colors, def_colors):
    """
    Resolve one combat round.
    Returns:
      (new_board, new_frontier, defender_damage,
       attack_groups, barrier_cells_initial,
       killed_def_cells, kills_per_column, breakthrough_columns,
       shift_kills_per_column,
       attacker_slide_map, defender_slide_map)
    """
    attack_groups = get_attack_groups(board, frontier, attacker_is_top, atk_colors)
    barrier_cells_initial = get_barrier_cells(
        board, frontier, defender_is_top=not attacker_is_top, chosen_colors=def_colors
    )

    # slide maps: (r_new, c) -> (r_old, is_new)
    attacker_slide_map = {}
    defender_slide_map = {}

    if not attack_groups:
        shift_kills = [0] * WIDTH
        return ([row[:] for row in board], frontier[:], 0,
                attack_groups, barrier_cells_initial,
                set(), [0]*WIDTH, set(), shift_kills,
                attacker_slide_map, defender_slide_map)

    damage_per_column = [0] * WIDTH
    attack_rows = {c: set() for c in range(WIDTH)}

    for g in attack_groups:
        c = g["col"]
        rows = g["rows"]
        L = len(rows)
        dmg = max(1, L - 2)  # 3->1, 4->2, etc.
        damage_per_column[c] += dmg
        attack_rows[c].update(rows)

    frontier_current = frontier[:]
    kills_per_column = [0] * WIDTH
    killed_def_cells = set()
    defender_damage = 0
    breakthrough_columns = set()
    regained_positions = set()

    killed_barrier_rows = set()

    for c in range(WIDTH):
        dmg = damage_per_column[c]
        fc = frontier_current[c]

        while dmg > 0:
            if attacker_is_top:
                front_def_row = fc + 1
                if front_def_row >= HEIGHT:
                    break

                if (front_def_row, c) in barrier_cells_initial:
                    # kill the entire barrier row across the board (single use per row)
                    if front_def_row not in killed_barrier_rows:
                        for cc in range(WIDTH):
                            if (front_def_row, cc) in barrier_cells_initial:
                                killed_def_cells.add((front_def_row, cc))
                                kills_per_column[cc] += 1
                        killed_barrier_rows.add(front_def_row)
                    dmg = 0
                    break
                else:
                    killed_def_cells.add((front_def_row, c))
                    kills_per_column[c] += 1
                    fc += 1
                    dmg -= 1

            else:  # attacker bottom
                front_def_row = fc
                if front_def_row < 0:
                    break

                if (front_def_row, c) in barrier_cells_initial:
                    if front_def_row not in killed_barrier_rows:
                        for cc in range(WIDTH):
                            if (front_def_row, cc) in barrier_cells_initial:
                                killed_def_cells.add((front_def_row, cc))
                                kills_per_column[cc] += 1
                        killed_barrier_rows.add(front_def_row)
                    dmg = 0
                    break
                else:
                    killed_def_cells.add((front_def_row, c))
                    kills_per_column[c] += 1
                    fc -= 1
                    dmg -= 1

        # Breakthrough logic
        if attacker_is_top and fc >= HEIGHT - 1:
            defender_damage += 1
            breakthrough_columns.add(c)
            old_fc = fc
            fc = max(fc - 3, -1)
            for r in range(fc + 1, old_fc + 1):
                if 0 <= r < HEIGHT:
                    regained_positions.add((r, c))
        elif (not attacker_is_top) and fc < 0:
            defender_damage += 1
            breakthrough_columns.add(c)
            old_fc = fc
            fc = min(fc + 3, HEIGHT - 1)
            for r in range(old_fc + 1, fc + 1):
                if 0 <= r < HEIGHT:
                    regained_positions.add((r, c))

        frontier_current[c] = fc

    # How many kills actually moved the frontier
    shift_kills_per_column = [0] * WIDTH
    for c in range(WIDTH):
        if attacker_is_top:
            shift_kills_per_column[c] = max(0, frontier_current[c] - frontier[c])
        else:
            shift_kills_per_column[c] = max(0, frontier[c] - frontier_current[c])

    # Build new board, with BOTH attackers and defenders sliding/filling
    new_board = [[0] * WIDTH for _ in range(HEIGHT)]

    for c in range(WIDTH):
        old_fc = frontier[c]
        new_fc = frontier_current[c]

        if attacker_is_top:
            # --- Attackers (top side) ---
            attackers_survivors = []
            for r in range(0, old_fc + 1):
                if r not in attack_rows[c]:
                    attackers_survivors.append(r)  # store row index

            k_surv = len(attackers_survivors)
            total_att_rows = max(0, new_fc + 1)
            back_fill = max(0, total_att_rows - k_surv)

            # New attackers at the very back
            for i in range(back_fill):
                if i < total_att_rows:
                    new_board[i][c] = random_color_index()
                    attacker_slide_map[(i, c)] = (i, True)

            # Survivors sliding toward frontier
            for j, r_old in enumerate(attackers_survivors):
                idx = back_fill + j
                if idx < total_att_rows:
                    new_board[idx][c] = board[r_old][c]
                    attacker_slide_map[(idx, c)] = (r_old, False)

            # --- Defenders (bottom side) ---
            def_survivors = []
            for r in range(old_fc + 1, HEIGHT):
                if (r, c) not in killed_def_cells:
                    def_survivors.append(r)

            def_surv_count = len(def_survivors)

            def_start = new_fc + 1
            if def_start < HEIGHT:
                def_len = HEIGHT - def_start

                # positions regained by defender in this column
                col_regained = [r for (r, cc) in regained_positions if cc == c]
                regained_count = len(col_regained)

                new_back = def_len - def_surv_count - regained_count
                if new_back < 0:
                    trim = -new_back
                    if trim < def_surv_count:
                        def_survivors = def_survivors[trim:]
                        def_surv_count = len(def_survivors)
                        new_back = 0
                    else:
                        def_survivors = []
                        def_surv_count = 0
                        new_back = 0

                pos = def_start

                # Regained squares near frontier: new defenders
                for _ in range(regained_count):
                    if pos >= HEIGHT:
                        break
                    new_board[pos][c] = random_color_index()
                    defender_slide_map[(pos, c)] = (pos, True)
                    pos += 1

                # Survivors sliding toward frontier
                for r_old in def_survivors:
                    if pos >= HEIGHT:
                        break
                    new_board[pos][c] = board[r_old][c]
                    defender_slide_map[(pos, c)] = (r_old, False)
                    pos += 1

                # New defenders at very back
                for _ in range(new_back):
                    if pos >= HEIGHT:
                        break
                    new_board[pos][c] = random_color_index()
                    defender_slide_map[(pos, c)] = (pos, True)
                    pos += 1

        else:
            # --- Attackers (bottom side) ---
            attackers_survivors = []
            for r in range(old_fc + 1, HEIGHT):
                if r not in attack_rows[c]:
                    attackers_survivors.append(r)

            k_surv = len(attackers_survivors)
            total_att_rows = max(0, HEIGHT - (new_fc + 1))
            back_fill = max(0, total_att_rows - k_surv)

            start_att = new_fc + 1

            # Survivors closest to frontier
            for j, r_old in enumerate(attackers_survivors):
                rpos = start_att + j
                if j < total_att_rows and rpos < HEIGHT:
                    new_board[rpos][c] = board[r_old][c]
                    attacker_slide_map[(rpos, c)] = (r_old, False)

            # New attackers at very back (bottommost)
            for i in range(back_fill):
                rpos = start_att + k_surv + i
                if rpos < HEIGHT:
                    new_board[rpos][c] = random_color_index()
                    attacker_slide_map[(rpos, c)] = (rpos, True)

            # --- Defenders (top side) ---
            def_survivors = []
            for r in range(0, old_fc + 1):
                if (r, c) not in killed_def_cells:
                    def_survivors.append(r)

            def_surv_count = len(def_survivors)

            def_end = new_fc
            if def_end >= 0:
                def_len = def_end + 1

                col_regained = [r for (r, cc) in regained_positions if cc == c]
                regained_count = len(col_regained)

                new_back = def_len - def_surv_count - regained_count
                if new_back < 0:
                    trim = -new_back
                    if trim < def_surv_count:
                        def_survivors = def_survivors[trim:]
                        def_surv_count = len(def_survivors)
                        new_back = 0
                    else:
                        def_survivors = []
                        def_surv_count = 0
                        new_back = 0

                pos = 0

                # Back: new random defenders
                for _ in range(new_back):
                    if pos > def_end:
                        break
                    new_board[pos][c] = random_color_index()
                    defender_slide_map[(pos, c)] = (pos, True)
                    pos += 1

                # Survivors sliding toward frontier
                for r_old in def_survivors:
                    if pos > def_end:
                        break
                    new_board[pos][c] = board[r_old][c]
                    defender_slide_map[(pos, c)] = (r_old, False)
                    pos += 1

                # Front-most: regained squares
                while pos <= def_end:
                    new_board[pos][c] = random_color_index()
                    defender_slide_map[(pos, c)] = (pos, True)
                    pos += 1

    return (new_board, frontier_current, defender_damage,
            attack_groups, barrier_cells_initial,
            killed_def_cells, kills_per_column, breakthrough_columns,
            shift_kills_per_column,
            attacker_slide_map, defender_slide_map)


# ---------- PYGAME DRAWING HELPERS ----------

def cell_rect(c, r):
    x = LEFT_MARGIN + c * CELL_SIZE
    y = TOP_UI + r * CELL_SIZE
    return pygame.Rect(x, y, CELL_SIZE, CELL_SIZE)


def draw_water(surface, color, rect, filled, alpha=255):
    surf = pygame.Surface(rect.size, pygame.SRCALPHA)
    w, h = rect.size
    cx, cy = w // 2, h // 2
    radius = min(w, h) // 4
    if filled:
        pygame.draw.circle(surf, color, (cx, cy + radius // 2), radius)
        pygame.draw.polygon(
            surf, color,
            [(cx, cy - radius * 2), (cx - radius, cy), (cx + radius, cy)]
        )
    else:
        pygame.draw.circle(surf, color, (cx, cy + radius // 2), radius, 3)
        pygame.draw.polygon(
            surf, color,
            [(cx, cy - radius * 2), (cx - radius, cy), (cx + radius, cy)],
            3
        )
    surf.set_alpha(alpha)
    surface.blit(surf, rect.topleft)


def draw_flame(surface, color, rect, filled, alpha=255):
    surf = pygame.Surface(rect.size, pygame.SRCALPHA)
    w, h = rect.size
    cx, cy = w // 2, h // 2
    pts = [
        (cx, cy - h // 3),
        (cx - w // 4, cy),
        (cx - w // 6, cy + h // 3),
        (cx + w // 6, cy + h // 3),
        (cx + w // 4, cy),
    ]
    if filled:
        pygame.draw.polygon(surf, color, pts)
    else:
        pygame.draw.polygon(surf, color, pts, 3)
    surf.set_alpha(alpha)
    surface.blit(surf, rect.topleft)


def draw_lightning(surface, color, rect, filled, alpha=255):
    surf = pygame.Surface(rect.size, pygame.SRCALPHA)
    w, h = rect.size
    cx = w // 2
    pts = [
        (cx - w // 8, 0),
        (cx + w // 8, 0),
        (cx, h // 2),
        (cx + w // 8, h // 2),
        (cx - w // 8, h),
        (cx - w // 4, h // 2),
        (cx - w // 8, h // 2),
    ]
    if filled:
        pygame.draw.polygon(surf, color, pts)
    else:
        pygame.draw.polygon(surf, color, pts, 3)
    surf.set_alpha(alpha)
    surface.blit(surf, rect.topleft)


def draw_tree(surface, color, rect, filled, alpha=255):
    surf = pygame.Surface(rect.size, pygame.SRCALPHA)
    w, h = rect.size
    cx, cy = w // 2, h // 2
    foliage = [
        (cx, cy - h // 3),
        (cx - w // 3, cy),
        (cx + w // 3, cy),
    ]
    trunk_rect = pygame.Rect(cx - w // 10, cy, w // 5, h // 3)
    if filled:
        pygame.draw.polygon(surf, color, foliage)
        pygame.draw.rect(surf, color, trunk_rect)
    else:
        pygame.draw.polygon(surf, color, foliage, 3)
        pygame.draw.rect(surf, color, trunk_rect, 3)
    surf.set_alpha(alpha)
    surface.blit(surf, rect.topleft)


def draw_cloud(surface, color, rect, filled, alpha=255):
    surf = pygame.Surface(rect.size, pygame.SRCALPHA)
    w, h = rect.size
    cx, cy = w // 2, h // 2
    r = min(w, h) // 6
    centers = [
        (cx - r * 2, cy),
        (cx, cy - r),
        (cx + r * 2, cy),
        (cx, cy + r),
    ]
    if filled:
        for ctr in centers:
            pygame.draw.circle(surf, color, ctr, r)
    else:
        for ctr in centers:
            pygame.draw.circle(surf, color, ctr, r, 2)
    surf.set_alpha(alpha)
    surface.blit(surf, rect.topleft)


def draw_color_icon(surface, color_idx, rect, filled=True, alpha=255):
    color = COLOR_DEFS[color_idx]["rgb"]
    if COLOR_DEFS[color_idx]["name"] == "Blue":
        draw_water(surface, color, rect, filled, alpha)
    elif COLOR_DEFS[color_idx]["name"] == "Red":
        draw_flame(surface, color, rect, filled, alpha)
    elif COLOR_DEFS[color_idx]["name"] == "Yellow":
        draw_lightning(surface, color, rect, filled, alpha)
    elif COLOR_DEFS[color_idx]["name"] == "Green":
        draw_tree(surface, color, rect, filled, alpha)
    else:
        draw_cloud(surface, color, rect, filled, alpha)


def draw_unit(
    surface,
    board_state,
    frontier_state,
    r,
    c,
    attackers_cells=None,
    fade_non_attack_strength=0.0,
    attacker_is_top=True,
    killed_cells=None,
    kill_fade_progress=0.0,
    post_fade_attacker_progress=None,
    fall_progress=None,
    extra_alpha_override=None,
):
    color_idx = board_state[r][c]
    base_color = COLOR_DEFS[color_idx]["rgb"]
    is_top = owner_is_top(r, c, frontier_state)
    filled = not is_top

    alpha = 255

    # Fading non-attacking attackers during phase 1
    if attackers_cells is not None and owner_is_top(r, c, frontier_state) == attacker_is_top:
        if (r, c) not in attackers_cells:
            alpha = int(alpha * max(0.0, 1.0 - fade_non_attack_strength))

    if post_fade_attacker_progress is not None:
        if owner_is_top(r, c, frontier_state) == attacker_is_top:
            alpha = int(255 * max(0.0, min(1.0, post_fade_attacker_progress)))

    if extra_alpha_override is not None:
        alpha = int(alpha * extra_alpha_override)

    if alpha <= 0:
        return

    rect = cell_rect(c, r)

    if COLOR_DEFS[color_idx]["name"] == "Blue":
        draw_water(surface, base_color, rect, filled, alpha)
    elif COLOR_DEFS[color_idx]["name"] == "Red":
        draw_flame(surface, base_color, rect, filled, alpha)
    elif COLOR_DEFS[color_idx]["name"] == "Yellow":
        draw_lightning(surface, base_color, rect, filled, alpha)
    elif COLOR_DEFS[color_idx]["name"] == "Green":
        draw_tree(surface, base_color, rect, filled, alpha)
    else:
        draw_cloud(surface, base_color, rect, filled, alpha)


def draw_frontier(surface, frontier_state):
    for c in range(WIDTH):
        f = frontier_state[c]
        if f < -1 or f >= HEIGHT - 1:
            continue
        y = TOP_UI + (f + 1) * CELL_SIZE
        x_start = LEFT_MARGIN + c * CELL_SIZE
        x_end = x_start + CELL_SIZE
        dash = 8
        x = x_start
        while x < x_end:
            pygame.draw.line(surface, WHITE, (x, y), (min(x + dash, x_end), y), 2)
            x += dash * 2


def draw_barrier_outline(surface, barrier_cells):
    by_row = {}
    for (r, c) in barrier_cells:
        by_row.setdefault(r, []).append(c)
    for r, cols in by_row.items():
        cols = sorted(cols)
        start = cols[0]
        prev = cols[0]
        for cc in cols[1:] + [None]:
            if cc is None or cc != prev + 1:
                left_rect = cell_rect(start, r)
                right_rect = cell_rect(prev, r)
                rect = left_rect.union(right_rect)
                pygame.draw.rect(surface, WHITE, rect, 3)
                if cc is not None:
                    start = cc
            prev = cc if cc is not None else prev


# ---------- UI BUTTONS ----------

class Button:
    def __init__(self, rect, text, color, text_color=BLACK):
        self.rect = pygame.Rect(rect)
        self.text = text
        self.color = color
        self.text_color = text_color
        self.font = pygame.font.SysFont(None, 24)

    def draw(self, surface, enabled=True):
        col = self.color if enabled else GRAY
        pygame.draw.rect(surface, col, self.rect, 0, border_radius=8)
        pygame.draw.rect(surface, BLACK, self.rect, 2, border_radius=8)
        text_surf = self.font.render(self.text, True, self.text_color)
        text_rect = text_surf.get_rect(center=self.rect.center)
        surface.blit(text_surf, text_rect)

    def is_clicked(self, pos):
        return self.rect.collidepoint(pos)


# ---------- MAIN GAME LOOP ----------

def main():
    pygame.init()
    screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
    pygame.display.set_caption("Mage Grid")
    clock = pygame.time.Clock()

    font = pygame.font.SysFont(None, 28)
    small_font = pygame.font.SysFont(None, 24)
    title_font = pygame.font.SysFont(None, 72)
    game_title_font = pygame.font.SysFont(None, 60)

    # Player names (top/bottom)
    top_name = "Player 1"
    bottom_name = "Player 2"

    # Title-screen input text
    p1_name_text = ""
    p2_name_text = ""
    active_input = 0  # 0 = Player1, 1 = Player2, None = no focus

    # Game state variables
    hp_top = STARTING_HP
    hp_bottom = STARTING_HP

    board, frontier = create_board()
    attacker_is_top = True
    round_num = 1

    STATE_TITLE = "TITLE"
    STATE_ATTACKER_SELECT = "ATTACKER_SELECT"
    STATE_DEFENDER_SELECT = "DEFENDER_SELECT"
    STATE_ANIMATING = "ANIMATING"
    STATE_GAME_OVER = "GAME_OVER"

    state = STATE_TITLE

    current_choices = []
    attacker_choices = None
    defender_choices = None

    attacker_locked = False

    # Color selection buttons (in-game)
    color_buttons = []
    btn_width = 80
    btn_height = 40
    gap = 8
    start_x = LEFT_MARGIN
    base_y = SCREEN_HEIGHT - BOTTOM_UI + 60

    for i, cdef in enumerate(COLOR_DEFS):
        rect = (
            start_x + i * (btn_width + gap),
            base_y,
            btn_width,
            btn_height
        )
        color_buttons.append(Button(rect, cdef["name"], cdef["rgb"], BLACK))

    last_btn_right = color_buttons[-1].rect.right
    confirm_button = Button(
        (last_btn_right + gap, base_y, 110, btn_height),
        "Confirm", (180, 180, 180)
    )

    # Exit button (in-game) - top right
    exit_button = Button(
        (SCREEN_WIDTH - 110 - 20, 10, 110, 40),
        "Exit", (200, 160, 160)
    )

    # Title-screen input boxes & Launch button
    input_width = 300
    input_height = 40
    input_gap_y = 40
    center_x = SCREEN_WIDTH // 2
    title_center_y = SCREEN_HEIGHT // 3

    p1_input_rect = pygame.Rect(
        center_x - input_width // 2,
        title_center_y + 60,
        input_width,
        input_height
    )
    p2_input_rect = pygame.Rect(
        center_x - input_width // 2,
        p1_input_rect.bottom + input_gap_y,
        input_width,
        input_height
    )

    launch_button = Button(
        (center_x - 80, p2_input_rect.bottom + 40, 160, 50),
        "Launch", (180, 180, 180)
    )

    # Animation context
    anim_start_time = None
    anim_attack_groups = []
    anim_attack_groups_sorted = []
    anim_barrier_cells = set()
    anim_defender_damage = 0
    anim_killed_def_cells = set()
    anim_pre_board = None
    anim_pre_frontier = None
    anim_kills_per_column = [0] * WIDTH
    anim_breakthrough_cols = set()
    anim_shift_kills_per_column = [0] * WIDTH
    anim_col_to_group_idx = {}
    anim_slide_map_attacker = {}
    anim_slide_map_defender = {}

    def start_new_game():
        nonlocal board, frontier, hp_top, hp_bottom, attacker_is_top, round_num
        nonlocal attacker_locked, attacker_choices, defender_choices
        nonlocal anim_attack_groups, anim_attack_groups_sorted, anim_barrier_cells
        nonlocal anim_killed_def_cells, anim_kills_per_column, anim_defender_damage
        nonlocal anim_shift_kills_per_column, anim_col_to_group_idx
        nonlocal anim_slide_map_attacker, anim_slide_map_defender

        board, frontier = create_board()
        hp_top = STARTING_HP
        hp_bottom = STARTING_HP
        attacker_is_top = True
        round_num = 1
        attacker_locked = False
        attacker_choices = None
        defender_choices = None
        anim_attack_groups = []
        anim_attack_groups_sorted = []
        anim_barrier_cells = set()
        anim_killed_def_cells = set()
        anim_kills_per_column = [0] * WIDTH
        anim_shift_kills_per_column = [0] * WIDTH
        anim_col_to_group_idx = {}
        anim_defender_damage = 0
        anim_slide_map_attacker = {}
        anim_slide_map_defender = {}

    def start_animation():
        nonlocal state, anim_start_time
        nonlocal anim_attack_groups, anim_attack_groups_sorted, anim_barrier_cells
        nonlocal anim_defender_damage, anim_killed_def_cells
        nonlocal anim_pre_board, anim_pre_frontier
        nonlocal anim_kills_per_column, anim_breakthrough_cols
        nonlocal anim_shift_kills_per_column, anim_col_to_group_idx
        nonlocal anim_slide_map_attacker, anim_slide_map_defender

        anim_pre_board = [row[:] for row in board]
        anim_pre_frontier = frontier[:]

        (new_board, new_frontier, dmg,
         groups, barriers_initial,
         killed_cells, kills_per_col, breakthrough_cols,
         shift_kills_per_col,
         attacker_slide_map, defender_slide_map) = resolve_combat(
            board, frontier, attacker_is_top, attacker_choices, defender_choices
        )

        board[:] = [row[:] for row in new_board]
        for i in range(len(frontier)):
            frontier[i] = new_frontier[i]

        anim_attack_groups = groups
        anim_barrier_cells = set(barriers_initial)
        anim_killed_def_cells = set(killed_cells)
        anim_defender_damage = dmg
        anim_kills_per_column = kills_per_col[:]
        anim_shift_kills_per_column = shift_kills_per_col[:]
        anim_breakthrough_cols = set(breakthrough_cols)

        anim_slide_map_attacker = attacker_slide_map
        anim_slide_map_defender = defender_slide_map

        def group_sort_key(g):
            c = g["col"]
            rows = g["rows"]
            if attacker_is_top:
                front_row = max(rows)
            else:
                front_row = min(rows)
            return (c, front_row)

        anim_attack_groups_sorted = sorted(anim_attack_groups, key=group_sort_key)

        anim_col_to_group_idx = {}
        for idx, g in enumerate(anim_attack_groups_sorted):
            c = g["col"]
            if c not in anim_col_to_group_idx:
                anim_col_to_group_idx[c] = idx

        state = STATE_ANIMATING
        anim_start_time = time.time()

    def draw_scene(
        board_state,
        frontier_state,
        fade_non_attack_strength=0.0,
        flash_red=False,
        show_barriers=False,
        post_fade_attacker_progress=None,
        formation_move_time=None,
        total_formations=0,
        slide_progress=None,
        slide_map_attacker=None,
        slide_map_defender=None,
    ):
        dynamic_frontier = list(frontier_state)
        fading_cells = {}
        conquered_cells = set()

        screen.fill(BLACK)

        # Title at top
        title_surf = game_title_font.render("MAGE GRID", True, WHITE)
        title_rect = title_surf.get_rect(center=(SCREEN_WIDTH // 2, 30))
        screen.blit(title_surf, title_rect)

        moving_indices = {}
        started_cells = set()

        # Attack formations moving
        if formation_move_time is not None and total_formations > 0 and anim_attack_groups_sorted:
            t = formation_move_time

            # Per-group timing and track which cells have started moving
            for i, g in enumerate(anim_attack_groups_sorted):
                start_i = i * (FORMATION_MOVE_PER + FORMATION_FADE_PER)
                mid_i = start_i + FORMATION_MOVE_PER
                end_i = mid_i + FORMATION_FADE_PER

                if t < start_i:
                    prog = None
                elif t < mid_i:
                    prog = (t - start_i) / max(FORMATION_MOVE_PER, 0.001)
                elif t < end_i:
                    prog = 1.0 + (t - mid_i) / max(FORMATION_FADE_PER, 0.001)
                else:
                    prog = None
                moving_indices[i] = prog

                # Any formation that has started moving should no longer be
                # drawn at its original cells in the static pass.
                if t >= start_i:
                    c = g["col"]
                    for r in g["rows"]:
                        started_cells.add((r, c))

            # Per-column frontier-stepping and fading defenders
            for c in range(WIDTH):
                kills_shift = anim_shift_kills_per_column[c]
                if kills_shift <= 0:
                    continue
                if c not in anim_col_to_group_idx:
                    continue
                idx = anim_col_to_group_idx[c]
                start_i = idx * (FORMATION_MOVE_PER + FORMATION_FADE_PER)
                mid_i = start_i + FORMATION_MOVE_PER

                if t <= start_i:
                    completed = 0
                    fade_idx = None
                    fade_frac = 0.0
                elif t >= mid_i:
                    completed = kills_shift
                    fade_idx = None
                    fade_frac = 0.0
                else:
                    group_t = t - start_i
                    normalized = (group_t / max(FORMATION_MOVE_PER, 0.001)) * kills_shift
                    completed = int(normalized)
                    if completed > kills_shift:
                        completed = kills_shift
                    fade_idx = completed if completed < kills_shift else None
                    fade_frac = normalized - completed

                f0 = anim_pre_frontier[c]
                if attacker_is_top:
                    dynamic_frontier[c] = f0 + completed
                else:
                    dynamic_frontier[c] = f0 - completed

                for j in range(completed):
                    if attacker_is_top:
                        r_conq = f0 + 1 + j
                    else:
                        r_conq = f0 - j
                    if 0 <= r_conq < HEIGHT:
                        conquered_cells.add((r_conq, c))

                if fade_idx is not None:
                    if attacker_is_top:
                        r_fade = f0 + 1 + fade_idx
                    else:
                        r_fade = f0 - fade_idx
                    if 0 <= r_fade < HEIGHT:
                        fading_cells[(r_fade, c)] = max(0.0, min(1.0, 1.0 - fade_frac))

        # Background
        for r in range(HEIGHT):
            for c in range(WIDTH):
                rect = cell_rect(c, r)
                if owner_is_top(r, c, dynamic_frontier):
                    bg = DARK_BROWN
                else:
                    bg = LIGHT_BROWN
                pygame.draw.rect(screen, bg, rect)

        draw_frontier(screen, dynamic_frontier)

        attack_cells_set = set()
        for g in anim_attack_groups:
            col = g["col"]
            for r in g["rows"]:
                attack_cells_set.add((r, col))

        # Static units (defenders and non-slide attackers)
        for r in range(HEIGHT):
            for c in range(WIDTH):
                # skip cells that belong to formations that have started moving
                if (r, c) in started_cells:
                    continue

                # skip cells that will be animated in slide phase for attacker/defender
                if slide_progress is not None:
                    if slide_map_attacker is not None and (r, c) in slide_map_attacker:
                        continue
                    if slide_map_defender is not None and (r, c) in slide_map_defender:
                        continue

                moving_here = False
                if formation_move_time is not None and total_formations > 0:
                    for i, g in enumerate(anim_attack_groups_sorted):
                        if moving_indices.get(i) is not None:
                            if g["col"] == c and r in g["rows"]:
                                moving_here = True
                                break
                if moving_here:
                    continue

                if (r, c) in conquered_cells:
                    continue

                extra_alpha = None
                if (r, c) in fading_cells:
                    extra_alpha = fading_cells[(r, c)]

                draw_unit(
                    screen,
                    board_state,
                    dynamic_frontier,
                    r,
                    c,
                    attackers_cells=attack_cells_set,
                    fade_non_attack_strength=fade_non_attack_strength,
                    attacker_is_top=attacker_is_top,
                    killed_cells=None,
                    kill_fade_progress=0.0,
                    post_fade_attacker_progress=None,
                    extra_alpha_override=extra_alpha,
                )

        # Moving formations
        if formation_move_time is not None and total_formations > 0:
            direction = 1 if attacker_is_top else -1
            for i, g in enumerate(anim_attack_groups_sorted):
                prog = moving_indices.get(i)
                if prog is None:
                    continue
                c = g["col"]
                rows = g["rows"]
                kills_col = anim_kills_per_column[c]
                if kills_col < 0:
                    kills_col = 0

                if attacker_is_top:
                    front_row = max(rows)
                    dist_to_frontier_cells = anim_pre_frontier[c] - front_row
                else:
                    front_row = min(rows)
                    dist_to_frontier_cells = front_row - (anim_pre_frontier[c] + 1)

                if dist_to_frontier_cells < 0:
                    dist_to_frontier_cells = 0

                total_cells = dist_to_frontier_cells + kills_col
                offset_total = direction * total_cells * CELL_SIZE

                if prog <= 1.0:
                    offset = offset_total * prog
                    alpha_factor = 1.0
                else:
                    offset = offset_total
                    fade_prog = prog - 1.0
                    fade_prog = max(0.0, min(1.0, fade_prog))
                    alpha_factor = max(0.0, 1.0 - fade_prog)

                for rr in rows:
                    rect = cell_rect(c, rr).move(0, offset)
                    color_idx = board_state[rr][c]
                    base_color = COLOR_DEFS[color_idx]["rgb"]
                    is_top = owner_is_top(rr, c, dynamic_frontier)
                    filled = not is_top
                    alpha = int(255 * alpha_factor)
                    if alpha <= 0:
                        continue
                    if COLOR_DEFS[color_idx]["name"] == "Blue":
                        draw_water(screen, base_color, rect, filled, alpha)
                    elif COLOR_DEFS[color_idx]["name"] == "Red":
                        draw_flame(screen, base_color, rect, filled, alpha)
                    elif COLOR_DEFS[color_idx]["name"] == "Yellow":
                        draw_lightning(screen, base_color, rect, filled, alpha)
                    elif COLOR_DEFS[color_idx]["name"] == "Green":
                        draw_tree(screen, base_color, rect, filled, alpha)
                    else:
                        draw_cloud(screen, base_color, rect, filled, alpha)

        # Slide phase: attackers + defenders
        if slide_progress is not None and (slide_map_attacker or slide_map_defender):
            stage = slide_progress * 3.0  # 0..3

            # --- attackers ---
            survivors_att = []
            new_units_att = []
            if slide_map_attacker:
                for (r_new, c), (r_old, is_new) in slide_map_attacker.items():
                    if is_new:
                        new_units_att.append((r_new, c))
                    else:
                        survivors_att.append((r_new, c, r_old))

            # --- defenders ---
            survivors_def = []
            new_units_def = []
            if slide_map_defender:
                for (r_new, c), (r_old, is_new) in slide_map_defender.items():
                    if is_new:
                        new_units_def.append((r_new, c))
                    else:
                        survivors_def.append((r_new, c, r_old))

            # Stage A: attackers fade-in at old positions; defenders visible at old positions
            if stage < 1.0:
                local = stage  # 0..1
                # attackers
                for r_new, c, r_old in survivors_att:
                    rect = cell_rect(c, r_old)
                    color_idx = anim_pre_board[r_old][c]
                    base_color = COLOR_DEFS[color_idx]["rgb"]
                    filled = not attacker_is_top
                    alpha = int(255 * local)
                    if alpha <= 0:
                        continue
                    if COLOR_DEFS[color_idx]["name"] == "Blue":
                        draw_water(screen, base_color, rect, filled, alpha)
                    elif COLOR_DEFS[color_idx]["name"] == "Red":
                        draw_flame(screen, base_color, rect, filled, alpha)
                    elif COLOR_DEFS[color_idx]["name"] == "Yellow":
                        draw_lightning(screen, base_color, rect, filled, alpha)
                    elif COLOR_DEFS[color_idx]["name"] == "Green":
                        draw_tree(screen, base_color, rect, filled, alpha)
                    else:
                        draw_cloud(screen, base_color, rect, filled, alpha)

                # defenders: show survivors at old positions, no fade (they never disappeared)
                for r_new, c, r_old in survivors_def:
                    rect = cell_rect(c, r_old)
                    color_idx = anim_pre_board[r_old][c]
                    base_color = COLOR_DEFS[color_idx]["rgb"]
                    is_top = owner_is_top(r_old, c, anim_pre_frontier)
                    filled = not is_top
                    alpha = 255
                    if COLOR_DEFS[color_idx]["name"] == "Blue":
                        draw_water(screen, base_color, rect, filled, alpha)
                    elif COLOR_DEFS[color_idx]["name"] == "Red":
                        draw_flame(screen, base_color, rect, filled, alpha)
                    elif COLOR_DEFS[color_idx]["name"] == "Yellow":
                        draw_lightning(screen, base_color, rect, filled, alpha)
                    elif COLOR_DEFS[color_idx]["name"] == "Green":
                        draw_tree(screen, base_color, rect, filled, alpha)
                    else:
                        draw_cloud(screen, base_color, rect, filled, alpha)
                # new units (both sides) invisible

            elif stage < 2.0:
                # Stage B: survivors slide to final positions
                local = stage - 1.0  # 0..1

                # attackers slide
                for r_new, c, r_old in survivors_att:
                    start_rect = cell_rect(c, r_old)
                    end_rect = cell_rect(c, r_new)
                    y = start_rect.y + (end_rect.y - start_rect.y) * local
                    rect = pygame.Rect(end_rect.x, int(y), CELL_SIZE, CELL_SIZE)
                    color_idx = board_state[r_new][c]
                    base_color = COLOR_DEFS[color_idx]["rgb"]
                    filled = not attacker_is_top
                    alpha = 255
                    if COLOR_DEFS[color_idx]["name"] == "Blue":
                        draw_water(screen, base_color, rect, filled, alpha)
                    elif COLOR_DEFS[color_idx]["name"] == "Red":
                        draw_flame(screen, base_color, rect, filled, alpha)
                    elif COLOR_DEFS[color_idx]["name"] == "Yellow":
                        draw_lightning(screen, base_color, rect, filled, alpha)
                    elif COLOR_DEFS[color_idx]["name"] == "Green":
                        draw_tree(screen, base_color, rect, filled, alpha)
                    else:
                        draw_cloud(screen, base_color, rect, filled, alpha)

                # defenders slide
                for r_new, c, r_old in survivors_def:
                    start_rect = cell_rect(c, r_old)
                    end_rect = cell_rect(c, r_new)
                    y = start_rect.y + (end_rect.y - start_rect.y) * local
                    rect = pygame.Rect(end_rect.x, int(y), CELL_SIZE, CELL_SIZE)
                    color_idx = board_state[r_new][c]
                    base_color = COLOR_DEFS[color_idx]["rgb"]
                    is_top = owner_is_top(r_new, c, frontier_state)
                    filled = not is_top
                    alpha = 255
                    if COLOR_DEFS[color_idx]["name"] == "Blue":
                        draw_water(screen, base_color, rect, filled, alpha)
                    elif COLOR_DEFS[color_idx]["name"] == "Red":
                        draw_flame(screen, base_color, rect, filled, alpha)
                    elif COLOR_DEFS[color_idx]["name"] == "Yellow":
                        draw_lightning(screen, base_color, rect, filled, alpha)
                    elif COLOR_DEFS[color_idx]["name"] == "Green":
                        draw_tree(screen, base_color, rect, filled, alpha)
                    else:
                        draw_cloud(screen, base_color, rect, filled, alpha)
                # new units still invisible

            else:
                # Stage C: survivors fixed at final, new units fade in (both sides)
                local = stage - 2.0  # 0..1
                # attackers survivors
                for r_new, c, r_old in survivors_att:
                    rect = cell_rect(c, r_new)
                    color_idx = board_state[r_new][c]
                    base_color = COLOR_DEFS[color_idx]["rgb"]
                    filled = not attacker_is_top
                    alpha = 255
                    if COLOR_DEFS[color_idx]["name"] == "Blue":
                        draw_water(screen, base_color, rect, filled, alpha)
                    elif COLOR_DEFS[color_idx]["name"] == "Red":
                        draw_flame(screen, base_color, rect, filled, alpha)
                    elif COLOR_DEFS[color_idx]["name"] == "Yellow":
                        draw_lightning(screen, base_color, rect, filled, alpha)
                    elif COLOR_DEFS[color_idx]["name"] == "Green":
                        draw_tree(screen, base_color, rect, filled, alpha)
                    else:
                        draw_cloud(screen, base_color, rect, filled, alpha)

                # defenders survivors
                for r_new, c, r_old in survivors_def:
                    rect = cell_rect(c, r_new)
                    color_idx = board_state[r_new][c]
                    base_color = COLOR_DEFS[color_idx]["rgb"]
                    is_top = owner_is_top(r_new, c, frontier_state)
                    filled = not is_top
                    alpha = 255
                    if COLOR_DEFS[color_idx]["name"] == "Blue":
                        draw_water(screen, base_color, rect, filled, alpha)
                    elif COLOR_DEFS[color_idx]["name"] == "Red":
                        draw_flame(screen, base_color, rect, filled, alpha)
                    elif COLOR_DEFS[color_idx]["name"] == "Yellow":
                        draw_lightning(screen, base_color, rect, filled, alpha)
                    elif COLOR_DEFS[color_idx]["name"] == "Green":
                        draw_tree(screen, base_color, rect, filled, alpha)
                    else:
                        draw_cloud(screen, base_color, rect, filled, alpha)

                # attackers new units fade in
                for r_new, c in new_units_att:
                    rect = cell_rect(c, r_new)
                    color_idx = board_state[r_new][c]
                    base_color = COLOR_DEFS[color_idx]["rgb"]
                    filled = not attacker_is_top
                    alpha = int(255 * local)
                    if alpha <= 0:
                        continue
                    if COLOR_DEFS[color_idx]["name"] == "Blue":
                        draw_water(screen, base_color, rect, filled, alpha)
                    elif COLOR_DEFS[color_idx]["name"] == "Red":
                        draw_flame(screen, base_color, rect, filled, alpha)
                    elif COLOR_DEFS[color_idx]["name"] == "Yellow":
                        draw_lightning(screen, base_color, rect, filled, alpha)
                    elif COLOR_DEFS[color_idx]["name"] == "Green":
                        draw_tree(screen, base_color, rect, filled, alpha)
                    else:
                        draw_cloud(screen, base_color, rect, filled, alpha)

                # defenders new units fade in
                for r_new, c in new_units_def:
                    rect = cell_rect(c, r_new)
                    color_idx = board_state[r_new][c]
                    base_color = COLOR_DEFS[color_idx]["rgb"]
                    is_top = owner_is_top(r_new, c, frontier_state)
                    filled = not is_top
                    alpha = int(255 * local)
                    if alpha <= 0:
                        continue
                    if COLOR_DEFS[color_idx]["name"] == "Blue":
                        draw_water(screen, base_color, rect, filled, alpha)
                    elif COLOR_DEFS[color_idx]["name"] == "Red":
                        draw_flame(screen, base_color, rect, filled, alpha)
                    elif COLOR_DEFS[color_idx]["name"] == "Yellow":
                        draw_lightning(screen, base_color, rect, filled, alpha)
                    elif COLOR_DEFS[color_idx]["name"] == "Green":
                        draw_tree(screen, base_color, rect, filled, alpha)
                    else:
                        draw_cloud(screen, base_color, rect, filled, alpha)

        # Barriers & red flash
        if show_barriers and anim_barrier_cells:
            draw_barrier_outline(screen, anim_barrier_cells)

        if flash_red:
            flash_surf = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT), pygame.SRCALPHA)
            flash_surf.fill((255, 0, 0, 80))
            screen.blit(flash_surf, (0, 0))

        # HUD
        if state in (STATE_ATTACKER_SELECT, STATE_DEFENDER_SELECT, STATE_ANIMATING, STATE_GAME_OVER):
            top_role = "ATTACKING" if attacker_is_top else "DEFENDING"
            bottom_role = "ATTACKING" if not attacker_is_top else "DEFENDING"
        else:
            top_role = ""
            bottom_role = ""

        battlefield_top_y = TOP_UI
        line_h = small_font.get_height() + 4

        top_name_s = small_font.render(top_name, True, WHITE)
        top_hp_s = small_font.render(f"HP: {hp_top}", True, WHITE)
        top_role_s = small_font.render(f"Role: {top_role}", True, WHITE)

        hud_left_x = 20
        screen.blit(top_name_s, (hud_left_x, battlefield_top_y))
        screen.blit(top_hp_s, (hud_left_x, battlefield_top_y + line_h))
        screen.blit(top_role_s, (hud_left_x, battlefield_top_y + 2 * line_h))

        battlefield_bottom_y = TOP_UI + HEIGHT * CELL_SIZE
        bottom_name_s = small_font.render(bottom_name, True, WHITE)
        bottom_hp_s = small_font.render(f"HP: {hp_bottom}", True, WHITE)
        bottom_role_s = small_font.render(f"Role: {bottom_role}", True, WHITE)

        role_y = battlefield_bottom_y - line_h
        hp_y = role_y - line_h
        name_y = hp_y - line_h

        hud_right_x = SCREEN_WIDTH - RIGHT_MARGIN + 20

        screen.blit(bottom_name_s, (hud_right_x, name_y))
        screen.blit(bottom_hp_s, (hud_right_x, hp_y))
        screen.blit(bottom_role_s, (hud_right_x, role_y))

        atk_name = top_name if attacker_is_top else bottom_name
        def_name = bottom_name if attacker_is_top else top_name

        if state == STATE_ATTACKER_SELECT:
            info = f"Round {round_num} - {atk_name} (ATTACKER): pick 2 colors"
        elif state == STATE_DEFENDER_SELECT:
            info = f"Round {round_num} - {def_name} (DEFENDER): pick 2 colors"
        elif state == STATE_ANIMATING:
            info = "Resolving combat..."
        elif state == STATE_GAME_OVER:
            winner = bottom_name if hp_top <= 0 else top_name
            info = f"Game Over! Winner: {winner}"
        else:
            info = ""

        if info:
            info_surf = font.render(info, True, WHITE)
            screen.blit(info_surf, (LEFT_MARGIN, SCREEN_HEIGHT - BOTTOM_UI + 5))

        if attacker_locked:
            lock_text = font.render("Attacker choices locked (hidden).", True, WHITE)
            screen.blit(lock_text, (LEFT_MARGIN, SCREEN_HEIGHT - BOTTOM_UI + 25))

        # Color buttons & confirm
        if state in (STATE_ATTACKER_SELECT, STATE_DEFENDER_SELECT):
            for idx, btn in enumerate(color_buttons):
                is_active = idx in current_choices
                if len(current_choices) >= 2 and not is_active:
                    enabled = False
                else:
                    enabled = True
                btn.draw(screen, enabled=enabled)

                if is_active:
                    icon_size = 32
                    icon_rect = pygame.Rect(
                        btn.rect.centerx - icon_size // 2,
                        btn.rect.bottom + 5,
                        icon_size,
                        icon_size
                    )
                    draw_color_icon(screen, idx, icon_rect, filled=True, alpha=255)

            confirm_enabled = (len(current_choices) == 2)
            confirm_button.draw(screen, enabled=confirm_enabled)

        if state != STATE_TITLE:
            exit_button.draw(screen, enabled=True)

        pygame.display.flip()

    def draw_title_screen():
        screen.fill(BLACK)

        title_surf = title_font.render("Mage Grid", True, WHITE)
        title_rect = title_surf.get_rect(center=(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 4))
        screen.blit(title_surf, title_rect)

        copy_surf = font.render("(c) Patrick Turley, 2025", True, WHITE)
        copy_rect = copy_surf.get_rect(center=(SCREEN_WIDTH // 2, title_rect.bottom + 25))
        screen.blit(copy_surf, copy_rect)

        label1 = font.render("Player 1:", True, WHITE)
        label2 = font.render("Player 2:", True, WHITE)
        screen.blit(label1, (p1_input_rect.left, p1_input_rect.top - 28))
        screen.blit(label2, (p2_input_rect.left, p2_input_rect.top - 28))

        def draw_input_box(rect, text, is_active):
            color = WHITE if is_active else GRAY
            pygame.draw.rect(screen, BLACK, rect, 0, border_radius=6)
            pygame.draw.rect(screen, color, rect, 2, border_radius=6)
            txt_surf = font.render(text, True, WHITE)
            txt_rect = txt_surf.get_rect(left=rect.left + 8, centery=rect.centery)
            screen.blit(txt_surf, txt_rect)

        draw_input_box(p1_input_rect, p1_name_text, active_input == 0)
        draw_input_box(p2_input_rect, p2_name_text, active_input == 1)

        launch_button.draw(screen, enabled=True)

        pygame.display.flip()

    # Main loop
    running = True
    while running:
        dt = clock.tick(FPS) / 1000.0

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
                break

            if state == STATE_TITLE:
                if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    mouse_pos = event.pos
                    if p1_input_rect.collidepoint(mouse_pos):
                        active_input = 0
                    elif p2_input_rect.collidepoint(mouse_pos):
                        active_input = 1
                    elif launch_button.is_clicked(mouse_pos):
                        name1 = p1_name_text.strip() or "Player 1"
                        name2 = p2_name_text.strip() or "Player 2"
                        top_name = name1[:NAME_MAX_LEN]
                        bottom_name = name2[:NAME_MAX_LEN]
                        start_new_game()
                        state = STATE_ATTACKER_SELECT
                        active_input = None

                elif event.type == pygame.KEYDOWN:
                    if active_input is not None:
                        if event.key == pygame.K_BACKSPACE:
                            if active_input == 0:
                                p1_name_text = p1_name_text[:-1]
                            else:
                                p2_name_text = p2_name_text[:-1]
                        elif event.key == pygame.K_RETURN:
                            name1 = p1_name_text.strip() or "Player 1"
                            name2 = p2_name_text.strip() or "Player 2"
                            top_name = name1[:NAME_MAX_LEN]
                            bottom_name = name2[:NAME_MAX_LEN]
                            start_new_game()
                            state = STATE_ATTACKER_SELECT
                            active_input = None
                        else:
                            ch = event.unicode
                            if ch and ch.isprintable():
                                if active_input == 0 and len(p1_name_text) < NAME_MAX_LEN:
                                    p1_name_text += ch
                                elif active_input == 1 and len(p2_name_text) < NAME_MAX_LEN:
                                    p2_name_text += ch
                continue

            # In-game events
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                mouse_pos = event.pos

                if exit_button.is_clicked(mouse_pos):
                    state = STATE_TITLE
                    continue

                if state in (STATE_ATTACKER_SELECT, STATE_DEFENDER_SELECT):
                    for idx, btn in enumerate(color_buttons):
                        if btn.is_clicked(mouse_pos):
                            is_active = (idx in current_choices)
                            if is_active:
                                current_choices.remove(idx)
                            else:
                                if len(current_choices) < 2:
                                    current_choices.append(idx)
                    if confirm_button.is_clicked(mouse_pos) and len(current_choices) == 2:
                        if state == STATE_ATTACKER_SELECT:
                            attacker_choices = tuple(current_choices)
                            attacker_locked = True
                            current_choices = []
                            state = STATE_DEFENDER_SELECT
                        else:
                            defender_choices = tuple(current_choices)
                            current_choices = []
                            start_animation()

        if state == STATE_TITLE:
            draw_title_screen()
            continue

        if state == STATE_ANIMATING:
            elapsed = time.time() - anim_start_time

            n_form = max(1, len(anim_attack_groups_sorted))
            total_form_time = n_form * (FORMATION_MOVE_PER + FORMATION_FADE_PER)

            T1 = FADE_OUT_DURATION
            T2 = T1 + total_form_time
            T3 = T2 + SLIDE_DURATION

            if elapsed < T1:
                fade_strength = elapsed / max(T1, 0.001)
                draw_scene(
                    anim_pre_board,
                    anim_pre_frontier,
                    fade_non_attack_strength=fade_strength,
                    flash_red=False,
                    show_barriers=True,
                    post_fade_attacker_progress=None,
                    formation_move_time=None,
                    total_formations=n_form,
                    slide_progress=None,
                    slide_map_attacker=None,
                    slide_map_defender=None,
                )
            elif elapsed < T2:
                local_t = elapsed - T1
                draw_scene(
                    anim_pre_board,
                    anim_pre_frontier,
                    fade_non_attack_strength=1.0,
                    flash_red=False,
                    show_barriers=True,
                    post_fade_attacker_progress=None,
                    formation_move_time=local_t,
                    total_formations=n_form,
                    slide_progress=None,
                    slide_map_attacker=None,
                    slide_map_defender=None,
                )
            elif elapsed < T3:
                slide_prog = (elapsed - T2) / max(SLIDE_DURATION, 0.001)
                slide_prog = max(0.0, min(1.0, slide_prog))
                # Flash red briefly at the very start of slide if there was damage
                flash = anim_defender_damage > 0 and slide_prog < 0.2
                draw_scene(
                    board,
                    frontier,
                    fade_non_attack_strength=0.0,
                    flash_red=flash,
                    show_barriers=False,
                    post_fade_attacker_progress=None,
                    formation_move_time=None,
                    total_formations=0,
                    slide_progress=slide_prog,
                    slide_map_attacker=anim_slide_map_attacker,
                    slide_map_defender=anim_slide_map_defender,
                )
            else:
                if attacker_is_top:
                    hp_bottom -= anim_defender_damage
                else:
                    hp_top -= anim_defender_damage

                if hp_top <= 0 or hp_bottom <= 0:
                    state = STATE_GAME_OVER
                else:
                    attacker_is_top = not attacker_is_top
                    attacker_locked = False
                    attacker_choices = None
                    defender_choices = None
                    anim_attack_groups = []
                    anim_attack_groups_sorted = []
                    anim_barrier_cells = set()
                    anim_killed_def_cells = set()
                    anim_kills_per_column = [0]*WIDTH
                    anim_shift_kills_per_column = [0]*WIDTH
                    anim_col_to_group_idx = {}
                    anim_defender_damage = 0
                    anim_slide_map_attacker = {}
                    anim_slide_map_defender = {}
                    round_num += 1
                    state = STATE_ATTACKER_SELECT
        else:
            draw_scene(
                board,
                frontier,
                fade_non_attack_strength=0.0,
                flash_red=False,
                show_barriers=False,
                post_fade_attacker_progress=None,
                formation_move_time=None,
                total_formations=0,
                slide_progress=None,
                slide_map_attacker=None,
                slide_map_defender=None,
            )

    pygame.quit()
    sys.exit()


if __name__ == "__main__":
    main()
