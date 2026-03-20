"""Battle Arena v2 - AI模块 (Deepseek API)"""

import json
import os
import re
import httpx

from game import GameEngine, Dot

DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"

SYSTEM_PROMPT = """你是一个回合制战术游戏的AI指挥官，控制若干"点子"在24x24地图上作战。

## 行动类型(每个点子每回合只能做一个)
1. move: 移动到周围1格(含对角线)。需指定direction。
2. melee: 近战攻击，对自身周围5x5范围内所有敌方造成伤害(=50%自身血量上限)。无需指定目标。
3. ranged: 向8方向之一发射子弹。子弹每回合飞3格，命中第一个敌方点子造成伤害(=25%自身血量上限)。
4. split: 分裂出新点子(血量上限减半)，最低4HP上限时无法再分裂。
5. rush: 消耗10%当前HP，突进到9x9范围内任意空格。需指定x,y坐标。
6. wait: 不行动。

## 方向名称
up, down, left, right, up-left, up-right, down-left, down-right

## 胜利条件
- 消灭所有敌方点子
- 或在地图中心胜利点区域积累足够分数(每个点子每回合+1分)

## 回复格式
严格回复JSON，不要输出其他内容。每个点子只出现一次:
{
  "actions": [
    {"dot": "A1", "type": "move", "direction": "right"},
    {"dot": "A2", "type": "melee"},
    {"dot": "A3", "type": "ranged", "direction": "up-right"},
    {"dot": "A4", "type": "split"},
    {"dot": "A5", "type": "rush", "x": 10, "y": 5},
    {"dot": "A6", "type": "wait"}
  ]
}

## 核心策略(按优先级排列, 非常重要, 请严格遵循)
1. 最高优先: 尽快向胜利点区域推进! 积分是最可靠的获胜方式。分裂后让多个点子冲向胜利点。
2. 开局第1-2回合: 优先split分裂2-3次, 增加点子数量
3. 分裂后: 大部分点子用move或rush朝胜利点方向移动, rush可以一次跳很远
4. 有敌方在5x5范围内: 用melee近战(伤害最高)
5. 敌方在同一直线/对角线上: 用ranged发射子弹骚扰
6. rush的坐标必须从"建议坐标"列表中选择, 不要自己编造坐标
7. 不要让所有点子都wait, 每回合至少要有行动
"""

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.txt")


def get_api_key() -> str:
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("DEEPSEEK_API_KEY="):
                    key = line.split("=", 1)[1].strip()
                    if key and key != "在这里填入你的key":
                        return key
    raise RuntimeError(
        "请在 config.txt 中填入你的 Deepseek API Key\n"
        f"  文件位置: {CONFIG_FILE}"
    )


def query_deepseek(game: GameEngine, team: int) -> list[dict]:
    """调用Deepseek API获取AI行动指令"""
    api_key = get_api_key()
    state = game.get_state_summary(team)

    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"当前游戏状态:\n\n{state}\n\n请为所有未行动的我方点子下达指令。"},
        ],
        "temperature": 0.5,
        "max_tokens": 2048,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        with httpx.Client(timeout=64) as client:
            resp = client.post(DEEPSEEK_API_URL, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            return parse_ai_response(content)
    except httpx.HTTPStatusError as e:
        print(f"[AI错误] API请求失败: {e.response.status_code} {e.response.text[:200]}")
        return []
    except Exception as e:
        print(f"[AI错误] {e}")
        return []


def parse_ai_response(content: str) -> list[dict]:
    json_match = re.search(r'\{[\s\S]*\}', content)
    if not json_match:
        print(f"[AI解析] 无法提取JSON, 原始回复: {content[:300]}")
        return []
    try:
        data = json.loads(json_match.group())
        return data.get("actions", [])
    except json.JSONDecodeError as e:
        print(f"[AI解析] JSON解析失败: {e}")
        return []


def execute_ai_actions(game: GameEngine, actions: list[dict]):
    """执行AI返回的行动指令"""
    team = game.current_team
    team_dots = {d.id: d for d in game.get_team_dots(team)}
    acted = set()

    for action in actions:
        dot_id = action.get("dot", "")
        action_type = action.get("type", "")

        if dot_id not in team_dots:
            continue
        if dot_id in acted:
            continue

        dot = team_dots[dot_id]
        err = None

        if action_type == "move":
            direction = action.get("direction", "")
            from game import DIRECTIONS, DIR_ALIASES
            dir_name = DIR_ALIASES.get(direction, direction)
            if dir_name in DIRECTIONS:
                dx, dy = DIRECTIONS[dir_name]
                err = game.execute_move(dot, dx, dy)
            else:
                err = f"无效方向: {direction}"

        elif action_type == "melee":
            err = game.execute_melee(dot)

        elif action_type == "ranged":
            direction = action.get("direction", "")
            err = game.execute_ranged(dot, direction)

        elif action_type == "split":
            err = game.execute_split(dot)

        elif action_type == "rush":
            x = action.get("x", 0)
            y = action.get("y", 0)
            err = game.execute_rush(dot, x, y)

        elif action_type == "wait":
            err = game.execute_wait(dot)

        else:
            err = f"未知行动类型: {action_type}"

        if err:
            game.log.append(f"[无效] {dot_id}: {err}")

        acted.add(dot_id)
