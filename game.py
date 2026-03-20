"""Battle Arena v2 - 游戏核心模块"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

# 8方向定义(米字形)
DIRECTIONS = {
    "up":         (0, -1),
    "down":       (0,  1),
    "left":       (-1, 0),
    "right":      (1,  0),
    "up-left":    (-1, -1),
    "up-right":   (1, -1),
    "down-left":  (-1,  1),
    "down-right": (1,  1),
}

# 方向缩写映射
DIR_ALIASES = {
    "u": "up", "d": "down", "l": "left", "r": "right",
    "ul": "up-left", "ur": "up-right", "dl": "down-left", "dr": "down-right",
}

# 方向 -> 子弹显示字符
DIR_CHARS = {
    (0, -1): "|", (0, 1): "|",
    (-1, 0): "-", (1, 0): "-",
    (-1, -1): "\\", (1, 1): "\\",
    (1, -1): "/", (-1, 1): "/",
}


@dataclass
class Dot:
    """点子(游戏单位)"""
    id: str           # 如 "A1", "B3"
    team: int         # 队伍编号 0-3
    x: int
    y: int
    hp: float = 128.0
    max_hp: float = 128.0

    @property
    def alive(self) -> bool:
        return self.hp > 0

    def can_split(self) -> bool:
        return self.max_hp > 4.0


@dataclass
class Bullet:
    """子弹"""
    team: int
    x: int
    y: int
    dx: int           # -1, 0, 1
    dy: int           # -1, 0, 1
    damage: float
    shooter_id: str   # 发射者ID(用于日志)


class GameMap:
    def __init__(self, size: int = 24):
        self.size = size
        # 胜利点区域: 偶数地图用偶数区域(6x6), 奇数地图用奇数区域(5x5)
        if size % 2 == 0:
            # 偶数: 中心在 (size/2-1, size/2) 之间, 用6x6覆盖
            half = size // 2
            vz_min = half - 3  # 24 -> 9
            vz_max = half + 2  # 24 -> 14
        else:
            # 奇数: 中心在 size//2, 用5x5覆盖
            center = size // 2
            vz_min = center - 2
            vz_max = center + 2
        self.vz_min = vz_min
        self.vz_max = vz_max
        self.victory_zone = set()
        for y in range(vz_min, vz_max + 1):
            for x in range(vz_min, vz_max + 1):
                self.victory_zone.add((x, y))

    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.size and 0 <= y < self.size

    def in_victory_zone(self, x: int, y: int) -> bool:
        return (x, y) in self.victory_zone


class GameEngine:
    MAX_TURNS = 200

    def __init__(self, config: dict):
        """
        config keys:
            map_size: int (default 24)
            num_players: int (2-4)
            player_types: list[str] ("ai" or "human")
            starting_dots: int (default 1)
            starting_hp: float (default 128.0)
            victory_threshold: int (default 24)
        """
        self.config = config
        map_size = config.get("map_size", 24)
        self.game_map = GameMap(map_size)
        self.num_players = config.get("num_players", 2)
        self.player_types = config.get("player_types", ["ai"] * self.num_players)
        self.victory_threshold = config.get("victory_threshold", 24)

        self.dots: list[Dot] = []
        self.bullets: list[Bullet] = []
        self.scores: dict[int, int] = {i: 0 for i in range(self.num_players)}
        self.turn = 0
        self.current_team = 0
        self.log: list[str] = []
        self.acted_dots: set[str] = set()
        self._next_dot_id: dict[int, int] = {i: 0 for i in range(self.num_players)}

        self._setup_dots(
            config.get("starting_dots", 1),
            config.get("starting_hp", 128.0),
        )

    def _team_letter(self, team: int) -> str:
        return chr(ord("A") + team)

    def _new_dot_id(self, team: int) -> str:
        self._next_dot_id[team] += 1
        return f"{self._team_letter(team)}{self._next_dot_id[team]}"

    def _setup_dots(self, starting_dots: int, starting_hp: float):
        s = self.game_map.size
        # 出生点: 对称分布, 距胜利点区域等距
        c = s // 2  # 中心=12
        offset = c - 3  # =9
        spawn_positions = [
            (c - offset, c - offset),  # 左上 (3,3)
            (c + offset, c + offset),  # 右下 (21,21)
            (c + offset, c - offset),  # 右上 (21,3)
            (c - offset, c + offset),  # 左下 (3,21)
        ]
        for team in range(self.num_players):
            sx, sy = spawn_positions[team]
            for i in range(starting_dots):
                dot_id = self._new_dot_id(team)
                # 多个起始点子时偏移
                ox = i % 3
                oy = i // 3
                self.dots.append(Dot(
                    id=dot_id, team=team,
                    x=sx + ox, y=sy + oy,
                    hp=starting_hp, max_hp=starting_hp,
                ))

    # --- 查询 ---

    def get_team_dots(self, team: int) -> list[Dot]:
        return [d for d in self.dots if d.team == team and d.alive]

    def get_all_alive_dots(self) -> list[Dot]:
        return [d for d in self.dots if d.alive]

    def dot_at(self, x: int, y: int) -> Dot | None:
        for d in self.dots:
            if d.alive and d.x == x and d.y == y:
                return d
        return None

    def has_acted(self, dot: Dot) -> bool:
        return dot.id in self.acted_dots

    # --- 胜负判定 ---

    def get_winner(self) -> int | None:
        """返回获胜队伍编号, 平局-1, 未结束None"""
        # 胜利点分数达标
        for team, score in self.scores.items():
            if score >= self.victory_threshold:
                return team

        # 只剩一个队伍有存活点子
        alive_teams = set(d.team for d in self.dots if d.alive)
        if len(alive_teams) == 1:
            return alive_teams.pop()
        if len(alive_teams) == 0:
            return -1

        if self.turn >= self.MAX_TURNS:
            # 按分数判
            max_score = max(self.scores.values())
            winners = [t for t, s in self.scores.items() if s == max_score]
            if len(winners) == 1:
                return winners[0]
            return -1

        return None

    # --- 回合阶段 ---

    def process_turn_start(self):
        """回合开始: 移动子弹 + 胜利点计分"""
        self._move_bullets()
        self._score_victory_points()

    def _move_bullets(self):
        """移动所有子弹，每颗走3步，逐步检测碰撞"""
        surviving = []
        for bullet in self.bullets:
            alive = True
            for _ in range(3):
                bullet.x += bullet.dx
                bullet.y += bullet.dy
                # 出界
                if not self.game_map.in_bounds(bullet.x, bullet.y):
                    alive = False
                    break
                # 碰撞检测
                hit = self.dot_at(bullet.x, bullet.y)
                if hit and hit.team != bullet.team:
                    hit.hp -= bullet.damage
                    self.log.append(
                        f"[子弹] {bullet.shooter_id}的子弹命中 {hit.id}, "
                        f"造成 {bullet.damage:.1f} 伤害 (剩余HP: {hit.hp:.1f})"
                    )
                    if not hit.alive:
                        hit.hp = 0.0
                        self.log.append(f"{hit.id} 被击败!")
                    alive = False
                    break
            if alive:
                surviving.append(bullet)
        self.bullets = surviving

    def _score_victory_points(self):
        """胜利点计分"""
        for dot in self.get_all_alive_dots():
            if self.game_map.in_victory_zone(dot.x, dot.y):
                self.scores[dot.team] += 1

    # --- 行动执行 ---

    def execute_move(self, dot: Dot, dx: int, dy: int) -> str | None:
        """移动点子(3x3范围, 1步), 成功返回None"""
        if err := self._check_can_act(dot):
            return err
        if abs(dx) > 1 or abs(dy) > 1 or (dx == 0 and dy == 0):
            return "移动范围: 周围1格(含对角线)"
        nx, ny = dot.x + dx, dot.y + dy
        if not self.game_map.in_bounds(nx, ny):
            return f"({nx},{ny})超出地图边界"
        if self.dot_at(nx, ny):
            return f"({nx},{ny})已被占据"
        old_x, old_y = dot.x, dot.y
        dot.x, dot.y = nx, ny
        self.acted_dots.add(dot.id)
        self.log.append(f"{dot.id} 移动 ({old_x},{old_y})->({nx},{ny})")
        return None

    def execute_melee(self, dot: Dot) -> str | None:
        """近战攻击: 5x5范围AoE, 伤害=50%自身血量上限, 只伤害敌方"""
        if err := self._check_can_act(dot):
            return err
        damage = dot.max_hp * 0.5
        hit_count = 0
        for target in self.get_all_alive_dots():
            if target.team == dot.team:
                continue
            if abs(target.x - dot.x) <= 2 and abs(target.y - dot.y) <= 2:
                target.hp -= damage
                hit_count += 1
                self.log.append(
                    f"{dot.id} 近战命中 {target.id}, "
                    f"造成 {damage:.1f} 伤害 (剩余HP: {target.hp:.1f})"
                )
                if not target.alive:
                    target.hp = 0.0
                    self.log.append(f"{target.id} 被击败!")
        self.acted_dots.add(dot.id)
        if hit_count == 0:
            self.log.append(f"{dot.id} 发动近战, 但范围内无敌方目标")
        return None

    def execute_ranged(self, dot: Dot, direction: str) -> str | None:
        """远程攻击: 向指定方向发射子弹"""
        if err := self._check_can_act(dot):
            return err
        # 解析方向
        dir_name = DIR_ALIASES.get(direction, direction)
        if dir_name not in DIRECTIONS:
            return f"无效方向 '{direction}', 可用: {', '.join(DIRECTIONS.keys())}"
        dx, dy = DIRECTIONS[dir_name]
        damage = dot.max_hp * 0.25
        bullet = Bullet(
            team=dot.team,
            x=dot.x, y=dot.y,  # 从当前位置出发(下回合开始移动)
            dx=dx, dy=dy,
            damage=damage,
            shooter_id=dot.id,
        )
        self.bullets.append(bullet)
        self.acted_dots.add(dot.id)
        self.log.append(f"{dot.id} 向{dir_name}发射子弹 (伤害:{damage:.1f})")
        return None

    def execute_split(self, dot: Dot) -> str | None:
        """分裂: 生成新点子, 双方血量上限减半"""
        if err := self._check_can_act(dot):
            return err
        if not dot.can_split():
            return f"血量上限已达最低(4), 无法继续分裂"
        # 找相邻空格
        empty = self._get_adjacent_empty(dot.x, dot.y)
        if not empty:
            return "周围无空位, 无法分裂"
        tx, ty = random.choice(empty)
        # 计算新血量
        old_max = dot.max_hp
        new_max = old_max / 2.0
        ratio = new_max / old_max
        dot.max_hp = new_max
        dot.hp = dot.hp * ratio
        new_id = self._new_dot_id(dot.team)
        new_dot = Dot(
            id=new_id, team=dot.team,
            x=tx, y=ty,
            hp=dot.hp,  # 与母体相同比例
            max_hp=new_max,
        )
        self.dots.append(new_dot)
        self.acted_dots.add(dot.id)
        self.log.append(
            f"{dot.id} 分裂 -> {new_id} @ ({tx},{ty}), "
            f"双方血量上限: {new_max:.1f}"
        )
        return None

    def execute_rush(self, dot: Dot, x: int, y: int) -> str | None:
        """突进: 消耗10%当前HP, 移动到9x9范围内"""
        if err := self._check_can_act(dot):
            return err
        if abs(x - dot.x) > 4 or abs(y - dot.y) > 4:
            return f"({x},{y})超出突进范围(9x9, 最远4格)"
        if x == dot.x and y == dot.y:
            return "目标位置与当前位置相同"
        if not self.game_map.in_bounds(x, y):
            return f"({x},{y})超出地图边界"
        if self.dot_at(x, y):
            return f"({x},{y})已被占据"
        cost = dot.hp * 0.1
        dot.hp -= cost
        if not dot.alive:
            dot.hp = 0.0
            self.acted_dots.add(dot.id)
            self.log.append(f"{dot.id} 突进时因HP不足而阵亡")
            return None
        old_x, old_y = dot.x, dot.y
        dot.x, dot.y = x, y
        self.acted_dots.add(dot.id)
        self.log.append(
            f"{dot.id} 突进 ({old_x},{old_y})->({x},{y}), "
            f"消耗 {cost:.1f} HP (剩余HP: {dot.hp:.1f})"
        )
        return None

    def execute_wait(self, dot: Dot) -> str | None:
        if err := self._check_can_act(dot):
            return err
        self.acted_dots.add(dot.id)
        self.log.append(f"{dot.id} 待命")
        return None

    def _check_can_act(self, dot: Dot) -> str | None:
        if not dot.alive:
            return f"{dot.id} 已阵亡"
        if dot.team != self.current_team:
            return f"{dot.id} 不属于当前行动队伍"
        if self.has_acted(dot):
            return f"{dot.id} 本回合已行动"
        return None

    def _get_adjacent_empty(self, cx: int, cy: int) -> list[tuple[int, int]]:
        result = []
        for dy in range(-1, 2):
            for dx in range(-1, 2):
                if dx == 0 and dy == 0:
                    continue
                nx, ny = cx + dx, cy + dy
                if self.game_map.in_bounds(nx, ny) and not self.dot_at(nx, ny):
                    result.append((nx, ny))
        return result

    def _get_rush_suggestions(self, dot: Dot) -> list[tuple[int, int]]:
        """为突进生成合法建议坐标, 优先朝胜利点方向"""
        vz = self.game_map
        mid = (vz.vz_min + vz.vz_max) // 2
        suggestions = []
        for target_x, target_y in [
            (mid, mid),
            (vz.vz_min, vz.vz_min),
            (vz.vz_max, vz.vz_max),
            (vz.vz_min, vz.vz_max),
            (vz.vz_max, vz.vz_min),
        ]:
            # 限制在9x9范围内
            rx = max(dot.x - 4, min(dot.x + 4, target_x))
            ry = max(dot.y - 4, min(dot.y + 4, target_y))
            if not self.game_map.in_bounds(rx, ry):
                continue
            if self.dot_at(rx, ry):
                continue
            if rx == dot.x and ry == dot.y:
                continue
            if (rx, ry) not in suggestions:
                suggestions.append((rx, ry))
        return suggestions[:4]

    def end_turn(self):
        self.acted_dots.clear()
        self.current_team = (self.current_team + 1) % self.num_players
        # 跳过已全灭的队伍
        checked = 0
        while not self.get_team_dots(self.current_team) and checked < self.num_players:
            self.current_team = (self.current_team + 1) % self.num_players
            checked += 1
        self.turn += 1

    # --- AI状态摘要 ---

    def get_state_summary(self, team: int) -> str:
        lines = []
        tl = self._team_letter(team)
        lines.append(f"=== 回合 {self.turn} | 你是队伍{tl}(编号{team}) ===")
        lines.append(f"地图: {self.game_map.size}x{self.game_map.size}")
        vz = self.game_map
        lines.append(f"胜利点: ({vz.vz_min}~{vz.vz_max}, {vz.vz_min}~{vz.vz_max}), 需要{self.victory_threshold}分")
        lines.append(f"当前分数: {', '.join(f'队伍{self._team_letter(t)}={s}' for t, s in self.scores.items())}")
        lines.append("")

        # 我方
        lines.append(f"【我方点子(队伍{tl})】")
        my_dots = self.get_team_dots(team)
        for d in my_dots:
            if self.has_acted(d):
                lines.append(f"  {d.id}: ({d.x},{d.y}) HP={d.hp:.1f}/{d.max_hp:.1f} [已行动]")
                continue
            lines.append(f"  {d.id}: ({d.x},{d.y}) HP={d.hp:.1f}/{d.max_hp:.1f}")
            # 列出可用行动
            opts = []
            # 移动选项
            moves = self._get_adjacent_empty(d.x, d.y)
            if moves:
                move_dirs = []
                for mx, my in moves:
                    ddx, ddy = mx - d.x, my - d.y
                    for name, (ndx, ndy) in DIRECTIONS.items():
                        if ndx == ddx and ndy == ddy:
                            move_dirs.append(name)
                            break
                opts.append(f"move方向: [{', '.join(move_dirs)}]")
            # 近战目标
            melee_targets = []
            for t in self.get_all_alive_dots():
                if t.team != team and abs(t.x - d.x) <= 2 and abs(t.y - d.y) <= 2:
                    melee_targets.append(t.id)
            if melee_targets:
                opts.append(f"melee范围内敌方: [{', '.join(melee_targets)}]")
            # 分裂
            if d.can_split() and moves:
                opts.append("可split")
            # 突进 - 列出几个关键候选坐标
            center = self.game_map.size // 2
            rush_targets = self._get_rush_suggestions(d)
            if rush_targets:
                opts.append(f"可rush(消耗10%HP): 建议坐标{rush_targets}")
            # 远程 - 计算哪些方向上有敌方
            ranged_dirs = []
            for dir_name, (ddx, ddy) in DIRECTIONS.items():
                # 沿该方向扫描
                for step in range(1, self.game_map.size):
                    cx, cy = d.x + ddx * step, d.y + ddy * step
                    if not self.game_map.in_bounds(cx, cy):
                        break
                    target = self.dot_at(cx, cy)
                    if target and target.team != team:
                        ranged_dirs.append(f"{dir_name}(命中{target.id},距离{step})")
                        break
                    if target and target.team == team:
                        break  # 友方挡住了
            if ranged_dirs:
                opts.append(f"ranged可命中: [{', '.join(ranged_dirs)}]")
            else:
                opts.append("可ranged(8方向发射子弹, 当前无直线目标)")
            for o in opts:
                lines.append(f"    {o}")

        # 敌方(不暴露HP)
        lines.append("【敌方点子(位置可见, 血量未知)】")
        for d in self.get_all_alive_dots():
            if d.team != team:
                lines.append(f"  {d.id}: ({d.x},{d.y}) 队伍{self._team_letter(d.team)}")

        # 子弹
        my_bullets = [b for b in self.bullets if b.team == team]
        enemy_bullets = [b for b in self.bullets if b.team != team]
        if my_bullets or enemy_bullets:
            lines.append("【场上子弹】")
            for b in my_bullets:
                lines.append(f"  [友方] ({b.x},{b.y}) 方向({b.dx},{b.dy})")
            for b in enemy_bullets:
                lines.append(f"  [敌方] ({b.x},{b.y}) 方向({b.dx},{b.dy})")

        if self.log:
            lines.append("【最近事件】")
            for entry in self.log[-8:]:
                lines.append(f"  {entry}")

        return "\n".join(lines)

    # --- 渲染 ---

    def render(self) -> str:
        s = self.game_map.size

        # 位置映射
        dot_map: dict[tuple[int, int], Dot] = {}
        for d in self.dots:
            if d.alive:
                dot_map[(d.x, d.y)] = d
        bullet_map: dict[tuple[int, int], Bullet] = {}
        for b in self.bullets:
            pos = (b.x, b.y)
            if pos not in dot_map:  # 点子优先显示
                bullet_map[pos] = b

        # 队伍颜色 (ANSI)
        team_colors = ["\033[94m", "\033[91m", "\033[92m", "\033[93m"]  # 蓝红绿黄
        reset = "\033[0m"
        dim = "\033[2m"

        lines = []

        # X轴十位
        tens_row = "    "
        for x in range(s):
            tens_row += f"{x // 10} " if x >= 10 else "  "
        lines.append(tens_row)

        # X轴个位
        ones_row = "    "
        for x in range(s):
            ones_row += f"{x % 10} "
        lines.append(ones_row)

        for y in range(s):
            row = f"{y:>3} "
            for x in range(s):
                pos = (x, y)
                in_vz = self.game_map.in_victory_zone(x, y)
                if pos in dot_map:
                    d = dot_map[pos]
                    color = team_colors[d.team % 4]
                    row += f"{color}@{reset} "
                elif pos in bullet_map:
                    b = bullet_map[pos]
                    char = DIR_CHARS.get((b.dx, b.dy), "*")
                    color = team_colors[b.team % 4]
                    row += f"{color}{char}{reset} "
                elif in_vz:
                    row += f"{dim}+{reset} "
                else:
                    row += ". "
            lines.append(row)

        # 图例
        lines.append("")
        legend = ""
        for i in range(self.num_players):
            tl = self._team_letter(i)
            color = team_colors[i % 4]
            legend += f"{color}@{reset}=队伍{tl}  "
        legend += f"{dim}+{reset}=胜利点  .=空地"
        lines.append(legend)

        # 分数
        score_line = "分数: "
        for i in range(self.num_players):
            tl = self._team_letter(i)
            score_line += f"{tl}={self.scores[i]}/{self.victory_threshold}  "
        lines.append(score_line)

        # 各队伍点子数量
        count_line = "点子: "
        for i in range(self.num_players):
            tl = self._team_letter(i)
            count = len(self.get_team_dots(i))
            count_line += f"{tl}={count}  "
        lines.append(count_line)

        return "\n".join(lines)
