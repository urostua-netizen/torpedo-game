"""魚雷ゲーム：OOPで作るターン制ASCII海戦ゲーム（Python標準ライブラリのみ）。

拡張ポイント：WEAPONS と Weapon.targets()/Weapon.scan_cells()、
             Ship のサブクラス、CPU の選択ロジック。

ゲーム全体を理解しなくても、新兵装なら既存の CrossTorpedo を参考に作れる。
"""
from __future__ import annotations

import argparse
from collections import Counter, deque
from dataclasses import dataclass, field
from itertools import zip_longest
import random
import time

SIZE = 9
RADAR_LIFETIME = 2  # 索敵した後のプレイヤー行動2回まで表示
SHIPS_PER_FLEET = 3  # 編成可能な隻数。2隻編成でも開始できる
DIRECTIONS = ((1, 0), (-1, 0), (0, 1), (0, -1))
NEIGHBORS = [(dx, dy) for dy in (-1, 0, 1) for dx in (-1, 0, 1) if dx or dy]


def in_bounds(p, size=SIZE):
    return 0 <= p[0] < size and 0 <= p[1] < size


def cells_in_rect(center, width, height):
    x, y = center
    return [(x + dx, y + dy)
            for dy in range(-(height // 2), height - height // 2)
            for dx in range(-(width // 2), width - width // 2)]


@dataclass
class Ship:
    name: str = '艦艇'
    symbol: str = 'S'
    width: int = 1
    height: int = 2
    weapons: tuple[str, ...] = ('torpedo',)
    positions: set[tuple[int, int]] = field(default_factory=set)
    hits: set[tuple[int, int]] = field(default_factory=set)

    def is_sunk(self):
        return bool(self.positions) and self.positions <= self.hits

    def is_alive(self):
        return bool(self.positions) and not self.is_sunk()

    def place(self, x, y, rotated=False):
        w, h = (self.height, self.width) if rotated else (self.width, self.height)
        return {(x + dx, y + dy) for dy in range(h) for dx in range(w)}


class AttackSubmarine(Ship):
    def __init__(self):
        super().__init__('攻撃型潜水艦', 'A', 1, 2, ('torpedo', 'cross'))


class StandardSubmarine(Ship):
    def __init__(self):
        super().__init__('通常型潜水艦', 'S', 1, 3, ('torpedo', 'double'))


class NuclearSubmarine(Ship):
    def __init__(self):
        super().__init__('原子力潜水艦', 'N', 1, 4, ('torpedo',))


class Carrier(Ship):
    def __init__(self):
        super().__init__('空母', 'C', 2, 4, ('radar',))


class Scout(Ship):
    def __init__(self):
        super().__init__('偵察機', 'P', 1, 1, ('radar', 'wide_radar', 'directional_radar'))


class Battleship(Ship):
    def __init__(self):
        super().__init__('戦艦', 'B', 2, 5, ('torpedo', 'line'))


class Destroyer(Ship):
    def __init__(self):
        super().__init__('駆逐艦', 'D', 1, 2, ('torpedo', 'diagonal'))


SHIP_TYPES = {
    '1': AttackSubmarine, '2': StandardSubmarine, '3': NuclearSubmarine,
    '4': Carrier, '5': Scout, '6': Battleship, '7': Destroyer,
}


class Weapon:
    key = ''
    name = ''
    hard_name = None
    limit = None  # None: 無制限
    kind = 'attack'

    def targets(self, center):
        return [center]

    def scan_cells(self, center, direction=None, hard_mode=False):
        return []


class Torpedo(Weapon):
    key, name, limit = 'torpedo', '通常魚雷', None


class DoubleTorpedo(Weapon):
    key, name, limit = 'double', '二連装魚雷', 3

    def targets(self, center):
        # 二連装は攻撃指定を2回行うため、Game側で別々の座標を受け取る
        return [center]


class CrossTorpedo(Weapon):
    key, name, limit = 'cross', '十字魚雷', 2

    def targets(self, center):
        x, y = center
        return [center] + [(x + dx, y + dy) for dx, dy in DIRECTIONS]


class DiagonalTorpedo(Weapon):
    key, name, limit = 'diagonal', 'X字魚雷', 2

    def targets(self, center):
        x, y = center
        return [center] + [(x + dx, y + dy) for dy in (-1, 1) for dx in (-1, 1)]


class LineTorpedo(Weapon):
    key, name, limit = 'line', '貫通魚雷(横3)', 2

    def targets(self, center):
        x, y = center
        return [(x + dx, y) for dx in (-1, 0, 1)]


class BasicRadar(Weapon):
    key, name, limit, kind = 'radar', '通常レーダー(3x3)', None, 'scan'
    hard_name = '通常レーダー(5x5)'

    def scan_cells(self, center, direction=None, hard_mode=False):
        size = 5 if hard_mode else 3
        return cells_in_rect(center, size, size)


class WideRadar(Weapon):
    key, name, limit, kind = 'wide_radar', '広域レーダー(5x5)', 1, 'scan'
    hard_name = '広域レーダー(7x7)'

    def scan_cells(self, center, direction=None, hard_mode=False):
        size = 7 if hard_mode else 5
        return cells_in_rect(center, size, size)


class DirectionalRadar(Weapon):
    key, name, limit, kind = 'directional_radar', '方向レーダー(直線5)', 2, 'scan'
    hard_name = '方向レーダー(直線9)'

    def scan_cells(self, center, direction=None, hard_mode=False):
        dx, dy = direction or (0, -1)
        return [(center[0] + n * dx, center[1] + n * dy)
                for n in range(9 if hard_mode else 5)]


# TODOの例：新しい魚雷を作るには Weapon を継承して targets() を変更し、
# WEAPONS に登録し、Shipサブクラスの weapons に key を追加する。
WEAPONS = {w.key: w() for w in (Torpedo, DoubleTorpedo, CrossTorpedo,
                                BasicRadar, WideRadar, DirectionalRadar,
                                DiagonalTorpedo, LineTorpedo)}


@dataclass
class RadarContact:
    """座標ごとの一時的な探知情報。艦影の有無と残り寿命を保持する。"""
    detected: bool
    remaining: int = RADAR_LIFETIME


class Fleet:
    def __init__(self, ships, size=SIZE, persistent_radar=False):
        self.ships = ships
        self.size = size
        self.persistent_radar = persistent_radar
        self.ammo = {k: w.limit for k, w in WEAPONS.items()}
        self.radar_logs = []
        self.radar_contacts: dict[tuple[int, int], RadarContact] = {}
        self.radar_last_scan = None  # (兵装名, 探知した艦影マス数)

    def advance_radar_time(self):
        """プレイヤーの有効な行動が1回終わったら既存の探知情報を減衰させる。"""
        if self.persistent_radar:
            return
        expired = []
        for p, contact in self.radar_contacts.items():
            contact.remaining -= 1
            if contact.remaining <= 0:
                expired.append(p)
        for p in expired:
            del self.radar_contacts[p]
        if not self.radar_contacts:
            self.radar_last_scan = None

    def record_radar_scan(self, cells, occupied, name):
        """既存情報の寿命を進めてから、今回の索敵結果を追加・上書きする。"""
        self.advance_radar_time()
        count = 0
        for p in cells:
            detected = p in occupied
            count += detected
            self.radar_contacts[p] = RadarContact(detected)
        self.radar_last_scan = (name, count)
        return count

    def occupied(self):
        return {p for ship in self.ships for p in ship.positions}

    def can_place(self, ship, positions):
        return all(in_bounds(p, self.size) for p in positions) and not (positions & self.occupied())

    def place(self, ship, x, y, rotate=False):
        positions = ship.place(x, y, rotate)
        if not self.can_place(ship, positions):
            return False
        ship.positions = positions
        return True

    def random_place_all(self, rng):
        for ship in self.ships:
            for _ in range(1000):
                if self.place(ship, rng.randrange(self.size), rng.randrange(self.size), rng.choice((True, False))):
                    break
            else:
                raise RuntimeError('艦隊を配置できません。艦数や盤面を確認してください。')

    def attack(self, p):
        """(結果, 撃沈した艦) を返す。外れの場合は近隣8マスの至近弾も検出。"""
        for ship in self.ships:
            if p in ship.positions:
                if p in ship.hits:
                    return 'repeat', None
                ship.hits.add(p)
                return ('sunk' if ship.is_sunk() else 'hit'), (ship if ship.is_sunk() else None)
        near = any((p[0] + dx, p[1] + dy) in self.occupied() for dx, dy in NEIGHBORS)
        return ('near' if near else 'miss'), None

    def has_offense(self):
        return any(s.is_alive() and any(WEAPONS[k].kind == 'attack' for k in s.weapons)
                   for s in self.ships)

    def available(self):
        keys = set()  # レーダーも艦種に紐づく。空母・偵察機の生存中のみ使用可能
        for ship in self.ships:
            if ship.is_alive():
                keys.update(ship.weapons)
        return [key for key in WEAPONS if key in keys and
                (self.ammo[key] is None or self.ammo[key] > 0)]

    def use_ammo(self, key):
        if key not in self.available():
            return False
        if self.ammo[key] is not None:
            self.ammo[key] -= 1
        return True

    def defeated(self):
        return all(s.is_sunk() for s in self.ships) or not self.has_offense()

    def summary(self):
        counts = Counter(s.name for s in self.ships)
        sunk = Counter(s.name for s in self.ships if s.is_sunk())
        return ' / '.join(f'{name} {n}隻 (撃沈{sunk[name]})' for name, n in counts.items())


class CPU:
    def __init__(self, rng, size=SIZE, difficulty='normal'):
        self.rng, self.size = rng, size
        self.difficulty = difficulty
        self.tried = set()
        self.priority = deque()
        self.hit_history = set()

    def choose_target(self):
        while self.priority:
            p = self.priority.popleft()
            if p not in self.tried and in_bounds(p, self.size):
                self.tried.add(p)
                return p
        remaining = [(x, y) for y in range(self.size) for x in range(self.size)
                     if (x, y) not in self.tried]
        if not remaining:
            return None
        p = self.rng.choice(remaining)
        self.tried.add(p)
        return p

    def feedback(self, p, result, sunk_positions=None):
        if self.difficulty == 'easy':
            return
        if self.difficulty == 'hard':
            if result == 'sunk':
                self.clear_sunk_tracking(p, sunk_positions)
                return
            if result == 'hit':
                self.track_hit(p)
                return
        if result in ('hit', 'near'):
            candidates = [(p[0] + dx, p[1] + dy) for dx, dy in NEIGHBORS]
            self.rng.shuffle(candidates)
            self.priority.extend(candidates)

    def track_hit(self, p):
        self.hit_history.add(p)
        x, y = p
        adjacent = [(x + dx, y + dy) for dx, dy in DIRECTIONS]
        extensions = []
        for axis in (((1, 0), (-1, 0)), ((0, 1), (0, -1))):
            if not any((x + dx, y + dy) in self.hit_history for dx, dy in axis):
                continue
            for dx, dy in axis:
                end = x + dx, y + dy
                while end in self.hit_history:
                    end = end[0] + dx, end[1] + dy
                extensions.append(end)
        # 大型艦は複数列にまたがるので、直線候補の後に周囲4マスも残す。
        for candidate in reversed(extensions + adjacent):
            if in_bounds(candidate, self.size) and candidate not in self.tried:
                self.priority.appendleft(candidate)

    def clear_sunk_tracking(self, p, sunk_positions):
        if sunk_positions is None:
            # 座標だけを渡す既存の呼び出しでは、連結した命中マスから推定する。
            sunk_positions = {p}
            pending = [p]
            while pending:
                x, y = pending.pop()
                for dx, dy in DIRECTIONS:
                    neighbor = x + dx, y + dy
                    if neighbor in self.hit_history and neighbor not in sunk_positions:
                        sunk_positions.add(neighbor)
                        pending.append(neighbor)
        self.hit_history.difference_update(sunk_positions)
        sunk_neighbors = {(x + dx, y + dy) for x, y in sunk_positions for dx, dy in DIRECTIONS}
        live_neighbors = {(x + dx, y + dy) for x, y in self.hit_history for dx, dy in DIRECTIONS}
        obsolete = (sunk_neighbors - live_neighbors) | sunk_positions
        self.priority = deque(p for p in self.priority if p not in obsolete)


DEPARTURE = r'''
                      |\
                 _____|_\________
         _______/_______________  \____
    ____/  _  _   _   _  _     \______\____
   /_________________________________________\
   \_________________________________________/
 ~~~~~~\_________________________________/~~~~~~~~
 ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
                  FLEET DEPARTURE
'''

SINKING = r'''
                 |\       *
         ________|_\___  ***
     ___/______________\_***___
  ~~~\____________________/~~~~~~~~~~~~~~~~
  ~~~~~\______________/~~~~~~~~~~~~~~~~~~~~
  ~~~~~~~~~~~\____/~~~~~~~~~~~~~~~~~~~~~~~~
                 ENEMY SUNK!
'''


def animation(ascii_art, enabled=True):
    if enabled:
        print('\n' + ascii_art)
        time.sleep(0.45)


def parse_coord(raw, size=SIZE):
    s = raw.strip().replace(',', ' ').split()
    if len(s) != 2:
        return None
    try:
        x, y = map(int, s)
    except ValueError:
        return None
    p = (x - 1, y - 1)
    return p if in_bounds(p, size) else None


def ask_coord(prompt, size=SIZE):
    while True:
        s = input(prompt).strip().lower()
        if s == 'q':
            raise KeyboardInterrupt
        p = parse_coord(s, size)
        if p is not None:
            return p
        print(f'座標は「列 行」で入力してください（例: 3 5、1〜{size}）。qで終了。')


def board_lines(fleet, shots=None, own=False, radar_contacts=None):
    shots = shots or {}
    occupied = {p: ship.symbol for ship in fleet.ships for p in ship.positions}
    radar_contacts = radar_contacts or {}
    tile_width = len(str(fleet.size))
    row_width = max(2, tile_width)
    rows = [' ' * (row_width + 2) +
            ' '.join(f'{i + 1:>{tile_width}}' for i in range(fleet.size))]
    for y in range(fleet.size):
        tiles = []
        for x in range(fleet.size):
            p = x, y
            if p in shots:
                tiles.append({'hit': 'X', 'sunk': 'X', 'miss': 'o', 'near': '?', 'repeat': 'X'}[shots[p]])
            elif own and p in occupied:
                tiles.append(occupied[p])
            elif p in radar_contacts:
                tiles.append('^' if radar_contacts[p].detected else '-')
            else:
                tiles.append('.')
        rows.append(f'{y + 1:>{row_width}}  ' +
                    ' '.join(f'{tile:>{tile_width}}' for tile in tiles))
    return rows


def show_boards(player, enemy, player_shots, cpu_shots, turn, reveal_enemy=False):
    left = board_lines(enemy, player_shots, own=reveal_enemy,
                       radar_contacts=player.radar_contacts)
    right = board_lines(player, cpu_shots, own=True)
    print(f'\n========== TURN {turn} / 魚雷ゲーム ==========')
    print('敵艦隊: ' + enemy.summary())
    print('自艦隊: ' + player.summary())
    left_width = max(26, len(left[0]))
    enemy_label = '敵の海域（' + ('DEBUG: 艦影公開' if reveal_enemy else '配置は秘密') + '）'
    if player.size != enemy.size:
        print(f'     {enemy_label} {enemy.size}×{enemy.size} / 自艦隊（被弾状況） {player.size}×{player.size}')
    else:
        print(f'     {enemy_label}         自艦隊（被弾状況）')
    for a, b in zip_longest(left, right, fillvalue=''):
        print(f'{a:<{left_width}} {b}'.rstrip())
    print('表示: X 命中  o 外れ  ? 至近弾  ^ 艦影反応  - 反応なし  . 未調査')
    if player.radar_contacts:
        if player.persistent_radar:
            print(f'レーダー探知済み: {len(player.radar_contacts)}マス（永続表示）')
        else:
            contact_counts = Counter(c.remaining for c in player.radar_contacts.values())
            time_info = ' / '.join(f'残り{n}行動:{count}マス' for n, count in sorted(contact_counts.items()))
            print(f'レーダー情報（探知位置を一時表示）: {time_info}')
        if player.radar_last_scan:
            name, count = player.radar_last_scan
            print(f'直近の索敵: {name} / 艦影反応 {count}マス')
    print('兵装: ' + ' / '.join(format_weapon_label(player, key) for key in player.available()))


def choose_fleet(size=SIZE):
    print('\n--- 艦隊編成 ---')
    ship_type_count = len(SHIP_TYPES)
    print(f'艦種は全{ship_type_count}種類。{size}×{size}の海域に2〜3隻を選択できます。')
    print('同じ艦種を複数選んでもOKです（例: 2 2 5）。')
    for k, ctor in SHIP_TYPES.items():
        s = ctor()
        print(f' {k}: {s.name} ({s.width}x{s.height}) | {", ".join(WEAPONS[w].name for w in s.weapons)}')
    print('※空母・偵察機は攻撃能力なし。通常レーダーは両艦とも無制限です。')
    print('※支援艦だけの編成も選択できますが、攻撃不能のため戦闘開始時に敗北します。')
    while True:
        s = input(f'{ship_type_count}種類から2〜3隻の番号を空白区切りで入力 (例: 1 4 5): ').strip()
        if s.lower() == 'q':
            raise KeyboardInterrupt
        values = s.split()
        if 2 <= len(values) <= SHIPS_PER_FLEET and all(v in SHIP_TYPES for v in values):
            ships = [SHIP_TYPES[v]() for v in values]
            if try_place_all(Fleet(ships, size), random.Random(0)):
                return [SHIP_TYPES[v]() for v in values]
            print(f'この艦隊は{size}×{size}の海域に配置できません。別の編成を選んでください。')
            continue
        print(f'番号1〜{ship_type_count}の中から、2〜3隻を選んでください。')


def try_place_all(fleet, rng):
    """狭い海域でも重複せず全艦配置できるか探索（成功時は配置する）。"""
    if sum(ship.width * ship.height for ship in fleet.ships) > fleet.size ** 2:
        return False
    ships = sorted(fleet.ships, key=lambda ship: ship.width * ship.height, reverse=True)
    occupied = set()

    def place_next(index):
        if index == len(ships):
            return True
        ship = ships[index]
        candidates = []
        for rotated in (False, True):
            for y in range(fleet.size):
                for x in range(fleet.size):
                    cells = ship.place(x, y, rotated)
                    if all(in_bounds(p, fleet.size) for p in cells) and not (cells & occupied):
                        if cells not in candidates:
                            candidates.append(cells)
        rng.shuffle(candidates)
        for cells in candidates:
            ship.positions = cells
            occupied.update(cells)
            if place_next(index + 1):
                return True
            occupied.difference_update(cells)
            ship.positions = set()
        return False

    if place_next(0):
        return True
    for ship in fleet.ships:
        ship.positions.clear()
    return False


def preview_placement(fleet, ship, x, y, rotate):
    """配置予定を重ねた盤面の行リストと、配置できるかどうかを返す。"""
    positions = ship.place(x, y, rotate)
    can_place = fleet.can_place(ship, positions)
    rows = board_lines(fleet, own=True)
    marker = '*' if can_place else '!'
    for row_index in range(fleet.size):
        row = rows[row_index + 1]
        tiles = row[4:].split()
        for column in range(fleet.size):
            if (column, row_index) in positions:
                tiles[column] = marker
        rows[row_index + 1] = row[:4] + ' '.join(tiles)
    if can_place:
        rows.append('* 配置予定')
    else:
        rows.append('! 配置不可（盤外または艦船が重複）。')
    return rows, can_place


def place_player_fleet(fleet, rng):
    s = input('配置方法 [a: 自動 / m: 手動] (Enterで自動): ').strip().lower()
    if s != 'm':
        if not try_place_all(fleet, rng):
            raise RuntimeError('艦船を配置できません。編成を変更してください。')
        return
    for ship in fleet.ships:
        while True:
            print('\n'.join(board_lines(fleet, own=True)))
            print(f'{ship.name} ({ship.width}x{ship.height}) を配置')
            p = ask_coord('左上の座標「列 行」: ', fleet.size)
            rotate = input('90度回転しますか？ [y/N] ').strip().lower() == 'y'
            rows, can_place = preview_placement(fleet, ship, *p, rotate)
            print('\n'.join(rows))
            if not can_place:
                continue
            confirm = input('この位置に確定しますか？ [Y/n] ').strip().lower()
            if confirm == 'q':
                raise KeyboardInterrupt
            if confirm in ('', 'y', 'yes') and fleet.place(ship, *p, rotate):
                break


def format_weapon_label(fleet, key):
    ammo = fleet.ammo[key]
    remaining = '∞' if ammo is None else f'残り{ammo}回'
    weapon = WEAPONS[key]
    name = (weapon.hard_name or weapon.name) if fleet.persistent_radar else weapon.name
    return f'{name} ({remaining})'


def offer_weapon(fleet):
    keys = fleet.available()
    print('\n兵装選択: ' + ' / '.join(
        f'{i + 1}:{format_weapon_label(fleet, key)}' for i, key in enumerate(keys)))
    while True:
        s = input('番号を入力（q終了）: ').strip().lower()
        if s == 'q':
            raise KeyboardInterrupt
        if s.isdigit() and 1 <= int(s) <= len(keys):
            return keys[int(s) - 1]
        print('表示された番号を選んでください。')


def perform_player_action(key, player, enemy, results, animations):
    weapon = WEAPONS[key]
    center = ask_coord('目標座標「列 行」: ', enemy.size)
    direction = None
    if key == 'directional_radar':
        while True:
            d = input('探索方向 [w上/a左/s下/d右]: ').strip().lower()
            if d in {'w': (0, -1), 'a': (-1, 0), 's': (0, 1), 'd': (1, 0)}:
                direction = {'w': (0, -1), 'a': (-1, 0), 's': (0, 1), 'd': (1, 0)}[d]
                break
            print('w/a/s/d を入力してください。')
    extra = None
    if key == 'double':
        extra = ask_coord('2発目の目標座標「列 行」: ', enemy.size)
    # 重複や盤外を除いた「実際に処理できるマス」を先に確認する
    targets = ([p for p in weapon.scan_cells(center, direction, hard_mode=player.persistent_radar)
                if in_bounds(p, enemy.size)]
               if weapon.kind == 'scan' else
               [p for p in weapon.targets(center) + ([extra] if extra else []) if in_bounds(p, enemy.size)])
    targets = list(dict.fromkeys(targets))
    if weapon.kind == 'attack' and all(p in results for p in targets):
        print('その範囲はすでに攻撃済み。弾薬を消費せず選び直せます。')
        return False
    if not player.use_ammo(key):
        print('この兵装は使用できません。')
        return False
    if weapon.kind == 'scan':
        name = (weapon.hard_name or weapon.name) if player.persistent_radar else weapon.name
        count = player.record_radar_scan(targets, enemy.occupied(), name)
        print(f'索敵結果: {len(targets)}マスを調査。艦影反応 {count} マス。')
        player.radar_logs.append((key, center, count))
        if player.persistent_radar:
            print(f'レーダー探知済み: {len(player.radar_contacts)}マス（永続表示）。艦影あり ^ / 反応なし -')
        else:
            print(f'艦影あり ^ / 反応なし - を表示（探索後の{RADAR_LIFETIME}行動まで有効）。')
        return True
    # 攻撃前の情報を使って座標を選べるよう、攻撃成立後に寿命を進める。
    player.advance_radar_time()
    for p in targets:
        label = f'({p[0] + 1},{p[1] + 1})'
        if p in results:
            print(f'{label}: 攻撃済みのためスキップ')
            continue
        outcome, sunk = enemy.attack(p)
        results[p] = outcome
        print(f'{label}: ' + {'hit': '命中！', 'sunk': '撃沈！', 'miss': '外れ', 'near': '至近弾！'}[outcome])
        if sunk:
            print('撃沈艦:', sunk.name)
            animation(SINKING, animations)
    return True


def cpu_turn(cpu, player, shots, animations):
    p = cpu.choose_target()
    if p is None:
        return
    outcome, sunk = player.attack(p)
    shots[p] = outcome
    if sunk and cpu.difficulty == 'hard':
        cpu.feedback(p, outcome, sunk_positions=sunk.positions)
    else:
        cpu.feedback(p, outcome)
    print(f'敵の攻撃 ({p[0] + 1},{p[1] + 1}): ' +
          {'hit': '命中！', 'sunk': '撃沈！', 'miss': '外れ', 'near': '至近弾！'}[outcome])
    if sunk:
        print('こちらの', sunk.name, 'が撃沈されました！')
        animation(SINKING, animations)


def make_debug_enemy_fleet(size):
    """4〜6マスの固定敵艦配置。命中・至近弾・撃沈を再現しやすくする。"""
    if size == 4:
        ships = [AttackSubmarine(), StandardSubmarine(), Scout()]
        placements = [(0, 0), (2, 1), (3, 0)]
    elif size == 5:
        ships = [AttackSubmarine(), StandardSubmarine(), NuclearSubmarine()]
        placements = [(0, 0), (2, 1), (4, 0)]
    elif size == 6:
        ships = [AttackSubmarine(), StandardSubmarine(), Carrier()]
        placements = [(0, 0), (2, 1), (4, 0)]
    else:
        raise ValueError('デバッグ海域は4×4、5×5、6×6から選択します。')
    fleet = Fleet(ships, size)
    for ship, (x, y) in zip(ships, placements):
        if not fleet.place(ship, x, y):
            raise AssertionError('デバッグ用の固定配置が不正です')
    return fleet


def make_cpu_fleet(rng, size=SIZE):
    # 攻撃可能艦は必ず含める（プレイヤー側は空母だけの編成も許可）
    attackers = [AttackSubmarine, StandardSubmarine, NuclearSubmarine, Battleship, Destroyer]
    pool = list(SHIP_TYPES.values())
    classes = [rng.choice(attackers), rng.choice(pool), rng.choice(pool)]
    fleet = Fleet([c() for c in classes], size)
    try:
        fleet.random_place_all(rng)
    except RuntimeError:
        if not try_place_all(fleet, rng):
            raise
    return fleet


def choose_difficulty():
    while True:
        choice = input(
            'CPUの難易度 [1: かんたん / 2: ふつう / 3: むずかしい] (Enterでふつう、qで終了): '
        ).strip().lower()
        if choice == 'q':
            raise KeyboardInterrupt
        if choice == '1':
            return 'easy'
        if choice in ('', '2'):
            return 'normal'
        if choice == '3':
            return 'hard'
        print('1、2、3のいずれかを入力してください。')


def run_game(rng, animations=True, auto_demo=False, debug_size=None):
    difficulty = 'normal'
    if auto_demo:
        player = Fleet([AttackSubmarine(), StandardSubmarine(), Carrier()])
        player.random_place_all(rng)
    else:
        print('\n========== FLEET COMMAND / 魚雷ゲーム ==========')
        size = debug_size or SIZE
        if debug_size:
            print(f'【DEBUG {size}×{size}】敵の艦影を公開／固定配置／敵の反撃なし。')
            print('命中やレーダーの検証用です。通常モードとはルールが異なります。')
        else:
            print(f'{len(SHIP_TYPES)}種類の艦種から2〜3隻を選んで{SIZE}x{SIZE}の海域に配置。'
                  '敵艦隊の編成は公開、位置は秘密です。')
            difficulty = choose_difficulty()
        print('命中X / 外れo / 至近弾? （周囲8マス以内に艦影あり）')
        player = Fleet(choose_fleet(size), size, persistent_radar=difficulty == 'hard')
        place_player_fleet(player, rng)
    if debug_size:
        enemy = make_debug_enemy_fleet(debug_size)
    elif difficulty == 'hard':
        enemy = make_cpu_fleet(rng, size=SIZE * 2)
    else:
        enemy = make_cpu_fleet(rng)
    animation(DEPARTURE, animations)
    player_shots, cpu_shots = {}, {}
    cpu = CPU(rng, player.size, difficulty=difficulty)
    turn = 0
    if not player.has_offense():
        print('この艦隊には攻撃手段がありません。出撃しましたが戦闘不能で敗北です。')
        return 'lose'
    while True:
        turn += 1
        if auto_demo and turn > player.size * player.size * 3 + 10:
            raise RuntimeError('終了できないゲーム状態を検出しました')
        if not auto_demo:
            show_boards(player, enemy, player_shots, cpu_shots, turn, reveal_enemy=bool(debug_size))
        action_finished = False
        while not action_finished:
            if auto_demo:
                keys = [k for k in player.available() if WEAPONS[k].kind == 'attack']
                key = 'torpedo' if 'torpedo' in keys else (keys[0] if keys else None)
                if key is None:
                    break
                pool = [(x, y) for y in range(enemy.size) for x in range(enemy.size) if (x, y) not in player_shots]
                if not pool:
                    break
                center = rng.choice(pool)
                outcome, sunk = enemy.attack(center)
                player_shots[center] = outcome
                if sunk and animations:
                    animation(SINKING, True)
                action_finished = True
            else:
                key = offer_weapon(player)
                action_finished = perform_player_action(key, player, enemy, player_shots, animations)
        if enemy.defeated():
            print('\n*** VICTORY! 敵艦隊を制圧しました！ ***')
            return 'win'
        if player.defeated():
            print('\n*** DEFEAT! 自艦隊は戦闘不能です。 ***')
            return 'lose'
        if debug_size:
            print('【DEBUG】敵の反撃はスキップ。次の検証へ進みます。')
            continue
        if not auto_demo:
            input('Enterで敵の反撃へ進む...')
        cpu_turn(cpu, player, cpu_shots, animations and not auto_demo)
        if player.defeated():
            print('\n*** DEFEAT! 自艦隊は戦闘不能です。 ***')
            return 'lose'


def main():
    parser = argparse.ArgumentParser(description='ASCII魚雷ゲーム (艦隊編成＋CPU戦)')
    parser.add_argument('--seed', type=int, default=None, help='乱数シード（動作確認用）')
    parser.add_argument('--no-animations', action='store_true', help='演出の待ち時間を省く')
    parser.add_argument('--demo', action='store_true', help='自動対戦で動作確認する')
    parser.add_argument('--debug', type=int, choices=(4, 5, 6), metavar='{4,5,6}',
                        help='検証モード: 小さな海域・敵の固定配置を公開・敵の反撃なし')
    args = parser.parse_args()
    if args.debug and args.demo:
        parser.error('--debug と --demo は同時に指定できません')
    rng = random.Random(args.seed)
    try:
        while True:
            outcome = run_game(rng, animations=not args.no_animations and not args.demo,
                               auto_demo=args.demo, debug_size=args.debug)
            if args.demo:
                print('DEMO RESULT:', outcome)
                break
            if input('\nもう一度遊びますか？ [y/N] ').strip().lower() != 'y':
                break
    except (KeyboardInterrupt, EOFError):
        print('\nゲームを終了しました。')


if __name__ == '__main__':
    main()
