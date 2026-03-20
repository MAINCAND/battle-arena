"""Battle Arena v2 - 主程序入口"""

import os
import time

from game import GameEngine, DIRECTIONS, DIR_ALIASES
from ai import query_deepseek, execute_ai_actions


def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def print_banner():
    print("=" * 50)
    print("   BATTLE ARENA v2")
    print("   回合制战术对战")
    print("=" * 50)
    print()


def get_game_config() -> dict:
    """交互式获取游戏配置"""
    config = {}

    # 地图大小
    print("-- 游戏设置 --")
    print()
    val = input("地图大小 (默认24): ").strip()
    config["map_size"] = int(val) if val.isdigit() and 8 <= int(val) <= 64 else 24

    # 玩家数量
    val = input("玩家数量 2-4 (默认2): ").strip()
    config["num_players"] = int(val) if val.isdigit() and 2 <= int(val) <= 4 else 2
    n = config["num_players"]

    # 每个玩家类型
    player_types = []
    print(f"\n为每个队伍设置控制方式 (ai/human):")
    for i in range(n):
        letter = chr(ord("A") + i)
        val = input(f"  队伍{letter} (默认ai): ").strip().lower()
        player_types.append("human" if val in ("human", "h", "人") else "ai")
    config["player_types"] = player_types

    # 开局点子数
    val = input(f"\n每队开局点子数 (默认1): ").strip()
    config["starting_dots"] = int(val) if val.isdigit() and 1 <= int(val) <= 8 else 1

    # 开局HP
    val = input("开局HP (默认128): ").strip()
    try:
        hp = float(val)
        config["starting_hp"] = hp if 4 <= hp <= 9999 else 128.0
    except ValueError:
        config["starting_hp"] = 128.0

    # 胜利分数
    val = input("胜利点所需分数 (默认24): ").strip()
    config["victory_threshold"] = int(val) if val.isdigit() and int(val) > 0 else 24

    print()
    print(f"地图: {config['map_size']}x{config['map_size']}")
    print(f"玩家: {n}队 ({', '.join(f'{chr(65+i)}={t}' for i, t in enumerate(player_types))})")
    print(f"开局: {config['starting_dots']}个点子, {config['starting_hp']}HP")
    print(f"胜利: 积{config['victory_threshold']}分 或 消灭所有敌方")
    print()
    input("按回车开始游戏...")
    return config


def get_player_actions(game: GameEngine, team: int):
    """获取人类玩家的行动指令"""
    letter = chr(ord("A") + team)
    dots = game.get_team_dots(team)
    if not dots:
        return

    print(f"\n--- 队伍{letter}的回合 (人类玩家) ---")
    print("指令:")
    print("  move 点子 方向        -- 移动1格 (up/down/left/right/ul/ur/dl/dr)")
    print("  melee 点子            -- 近战(5x5范围AoE)")
    print("  ranged 点子 方向      -- 远程射击")
    print("  split 点子            -- 分裂")
    print("  rush 点子 x y         -- 突进(9x9范围, 消耗10%HP)")
    print("  wait 点子             -- 跳过")
    print("  done                  -- 结束回合")
    print("  status / map / help")
    pending = [d.id for d in dots if not game.has_acted(d)]
    print(f"待行动: {', '.join(pending)}")
    print()

    while True:
        pending = [d.id for d in dots if not game.has_acted(d)]
        if not pending:
            print("所有点子已行动, 回合结束")
            break

        cmd = input("> ").strip()
        if not cmd:
            continue
        parts = cmd.split()
        action = parts[0].lower()

        if action == "done":
            break
        if action == "map":
            print(game.render())
            continue
        if action == "status":
            print(game.get_state_summary(team))
            continue
        if action == "help":
            print("  move A1 right    -- 向右移动")
            print("  melee A1         -- 近战攻击5x5范围")
            print("  ranged A1 up     -- 向上发射子弹")
            print("  split A1         -- 分裂")
            print("  rush A1 10 12    -- 突进到(10,12)")
            print("  wait A1          -- 跳过")
            continue

        if len(parts) < 2:
            print("格式错误, 输入 help 查看帮助")
            continue

        dot_id = parts[1].upper()
        dot = None
        for d in dots:
            if d.id == dot_id:
                dot = d
                break
        if not dot:
            print(f"点子 {dot_id} 不存在或已阵亡")
            continue
        if game.has_acted(dot):
            print(f"{dot_id} 本回合已行动")
            continue

        err = None

        if action == "move":
            if len(parts) < 3:
                print("格式: move 点子 方向")
                continue
            direction = parts[2].lower()
            dir_name = DIR_ALIASES.get(direction, direction)
            if dir_name not in DIRECTIONS:
                print(f"无效方向, 可用: up/down/left/right/ul/ur/dl/dr")
                continue
            dx, dy = DIRECTIONS[dir_name]
            err = game.execute_move(dot, dx, dy)

        elif action == "melee":
            err = game.execute_melee(dot)

        elif action == "ranged":
            if len(parts) < 3:
                print("格式: ranged 点子 方向")
                continue
            err = game.execute_ranged(dot, parts[2].lower())

        elif action == "split":
            err = game.execute_split(dot)

        elif action == "rush":
            if len(parts) < 4:
                print("格式: rush 点子 x y")
                continue
            try:
                x, y = int(parts[2]), int(parts[3])
            except ValueError:
                print("坐标必须是整数")
                continue
            err = game.execute_rush(dot, x, y)

        elif action == "wait":
            err = game.execute_wait(dot)

        else:
            print("未知指令, 输入 help 查看帮助")
            continue

        if err:
            print(f"  失败: {err}")
        else:
            # 显示最新日志
            if game.log:
                print(f"  {game.log[-1]}")


def run_ai_turn(game: GameEngine, team: int):
    """AI自动行动"""
    letter = chr(ord("A") + team)
    dot_count = len(game.get_team_dots(team))
    print(f"\n[AI] 队伍{letter} ({dot_count}个点子) 思考中...")

    log_before = len(game.log)
    actions = query_deepseek(game, team)
    if actions:
        execute_ai_actions(game, actions)
        new_entries = game.log[log_before:]
        if new_entries:
            print(f"队伍{letter} 的行动:")
            for entry in new_entries:
                print(f"  {entry}")
        else:
            print(f"  队伍{letter} 无有效行动")
    else:
        print(f"  队伍{letter} AI未返回有效指令, 跳过")


def game_loop(config: dict):
    """主游戏循环"""
    game = GameEngine(config)

    print("\n游戏开始!")
    print(game.render())
    time.sleep(1)

    while game.get_winner() is None:
        team = game.current_team
        letter = chr(ord("A") + team)

        print(f"\n{'='*40}")
        print(f"  回合 {game.turn} | 队伍{letter}的回合")
        score_info = "  分数: " + " ".join(
            f"{chr(65+t)}={s}" for t, s in game.scores.items()
        )
        print(score_info)
        print(f"{'='*40}")

        # 回合开始处理(子弹移动+计分)
        game.process_turn_start()

        # 检查子弹/计分阶段是否产生了胜负变化
        if game.get_winner() is not None:
            break

        player_type = game.player_types[team]
        if player_type == "human":
            print(game.render())
            get_player_actions(game, team)
        else:
            run_ai_turn(game, team)
            # 所有AI回合之间短暂停顿
            all_ai = all(t == "ai" for t in game.player_types)
            if all_ai:
                print(game.render())
                time.sleep(1.5)

        game.end_turn()

    # 游戏结束
    winner = game.get_winner()
    print("\n" + "=" * 50)
    if winner == -1:
        print("  游戏结束: 平局!")
    else:
        letter = chr(ord("A") + winner)
        print(f"  游戏结束! 队伍{letter} 获胜!")
    # 显示最终分数
    for t, s in game.scores.items():
        tl = chr(ord("A") + t)
        dots = len(game.get_team_dots(t))
        print(f"  队伍{tl}: {s}分, {dots}个存活点子")
    print("=" * 50)
    print(game.render())


def main():
    clear_screen()
    print_banner()
    config = get_game_config()
    game_loop(config)


if __name__ == "__main__":
    main()
