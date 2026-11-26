"""魚雷ゲームの最低限の回帰テスト：python -m unittest discover -s tests -v"""
import random
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from torpedo_game import (AttackSubmarine, BasicRadar, Carrier, CPU, CrossTorpedo,
                          Fleet, NuclearSubmarine, Scout, StandardSubmarine,
                          WEAPONS, SHIP_TYPES, choose_fleet, make_cpu_fleet, make_debug_enemy_fleet,
                          try_place_all, board_lines, parse_coord, run_game)


class TorpedoGameTests(unittest.TestCase):
    def test_ship_sizes_and_weapons(self):
        for cls, expected in [(AttackSubmarine, 2), (StandardSubmarine, 3),
                              (NuclearSubmarine, 4), (Carrier, 8), (Scout, 1)]:
            ship = cls()
            self.assertEqual(len(ship.place(0, 0)), expected)
            self.assertEqual(len(ship.place(0, 0, True)), expected)
        self.assertNotIn('torpedo', Carrier().weapons)

    def test_placement_reject_overlap_and_outside(self):
        a, b = AttackSubmarine(), StandardSubmarine()
        fleet = Fleet([a, b])
        self.assertTrue(fleet.place(a, 0, 0))
        self.assertFalse(fleet.place(b, 0, 0))
        self.assertFalse(fleet.place(b, 9, 8))
        self.assertTrue(fleet.place(b, 2, 0))

    def test_hit_near_and_sunk(self):
        ship = AttackSubmarine()
        fleet = Fleet([ship])
        fleet.place(ship, 4, 4)
        self.assertEqual(fleet.attack((3, 3))[0], 'near')
        self.assertEqual(fleet.attack((0, 0))[0], 'miss')
        self.assertEqual(fleet.attack((4, 4))[0], 'hit')
        self.assertEqual(fleet.attack((4, 4))[0], 'repeat')
        self.assertEqual(fleet.attack((4, 5))[0], 'sunk')
        self.assertTrue(ship.is_sunk())
        self.assertTrue(fleet.defeated())

    def test_lost_sub_removes_weapon(self):
        a, b = AttackSubmarine(), StandardSubmarine()
        f = Fleet([a, b])
        f.place(a, 1, 1)
        f.place(b, 4, 4)
        self.assertIn('cross', f.available())
        self.assertIn('double', f.available())
        for p in a.positions:
            f.attack(p)
        self.assertNotIn('cross', f.available())
        self.assertIn('double', f.available())
        self.assertFalse(f.defeated())

    def test_support_only_is_defeated(self):
        f = Fleet([Carrier(), Carrier(), Scout()])
        f.random_place_all(random.Random(2))
        self.assertFalse(f.has_offense())
        self.assertTrue(f.defeated())
        self.assertIn('radar', f.available())
        self.assertIn('wide_radar', f.available())

    def test_radar_availability_by_ship_type(self):
        for cls in (Carrier, Scout):
            ship = cls()
            fleet = Fleet([ship]); fleet.place(ship, 0, 0)
            self.assertIn('radar', fleet.available())
            self.assertIsNone(fleet.ammo['radar'])
            for _ in range(10):
                self.assertTrue(fleet.use_ammo('radar'))
            self.assertIsNone(fleet.ammo['radar'])
            if cls is Carrier:
                self.assertNotIn('wide_radar', fleet.available())
                self.assertNotIn('directional_radar', fleet.available())
            else:
                self.assertIn('wide_radar', fleet.available())
                self.assertIn('directional_radar', fleet.available())

    def test_radar_removed_after_support_ship_sinks(self):
        carrier, scout, sub = Carrier(), Scout(), AttackSubmarine()
        fleet = Fleet([carrier, scout, sub])
        fleet.place(carrier, 0, 0)
        fleet.place(scout, 4, 0)
        fleet.place(sub, 7, 0)
        for p in scout.positions:
            fleet.attack(p)
        self.assertIn('radar', fleet.available())
        self.assertNotIn('wide_radar', fleet.available())
        self.assertNotIn('directional_radar', fleet.available())
        for p in carrier.positions:
            fleet.attack(p)
        self.assertNotIn('radar', fleet.available())
        self.assertIn('torpedo', fleet.available())

    def test_submarines_have_no_radar_without_support(self):
        sub = StandardSubmarine()
        fleet = Fleet([sub]); fleet.place(sub, 2, 2)
        self.assertNotIn('radar', fleet.available())
        self.assertFalse(fleet.use_ammo('radar'))

    def test_eight_ship_types_and_three_ship_limit(self):
        self.assertEqual(len(SHIP_TYPES), 8)
        with patch('builtins.input', side_effect=['4 5 3']):
            with redirect_stdout(StringIO()):
                ships = choose_fleet()
        self.assertEqual([type(s) for s in ships], [Carrier, Scout, NuclearSubmarine])
        with patch('builtins.input', side_effect=['1 2 3 4', '5 5']):
            with redirect_stdout(StringIO()):
                ships = choose_fleet()
        self.assertEqual([type(s) for s in ships], [Scout, Scout])

    def test_ammo_limits(self):
        a = AttackSubmarine()
        f = Fleet([a]); f.place(a, 1, 1)
        self.assertEqual(f.ammo['cross'], 2)
        self.assertTrue(f.use_ammo('cross'))
        self.assertTrue(f.use_ammo('cross'))
        self.assertFalse(f.use_ammo('cross'))
        self.assertNotIn('cross', f.available())

    def test_cross_torpedo_edge(self):
        coords = CrossTorpedo().targets((0, 0))
        self.assertEqual(len(coords), 5)
        self.assertEqual(sum(0 <= x < 9 and 0 <= y < 9 for x, y in coords), 3)

    def test_radar_shape(self):
        self.assertEqual(len(BasicRadar().scan_cells((4, 4))), 9)
        self.assertEqual(len(WEAPONS['wide_radar'].scan_cells((4, 4))), 25)
        self.assertEqual(len(WEAPONS['directional_radar'].scan_cells((4, 4), (1, 0))), 5)

    def test_cpu_no_duplicate_and_near_clue(self):
        cpu = CPU(random.Random(1))
        p = cpu.choose_target()
        cpu.feedback(p, 'near')
        self.assertNotEqual(cpu.choose_target(), p)
        selected = {p}
        selected.add(next(iter(cpu.tried - {p})))
        while len(selected) < 81:
            q = cpu.choose_target()
            self.assertNotIn(q, selected)
            selected.add(q)
        self.assertIsNone(cpu.choose_target())

    def test_coord_validation(self):
        self.assertEqual(parse_coord('4 7'), (3, 6))
        self.assertEqual(parse_coord('4,7'), (3, 6))
        for s in ('0 1', '10 1', '4', 'a b'):
            self.assertIsNone(parse_coord(s))

    def test_all_random_cpu_fleets_are_valid(self):
        for seed in range(150):
            fleet = make_cpu_fleet(random.Random(seed))
            occupied = set()
            for s in fleet.ships:
                self.assertTrue(s.positions)
                self.assertTrue(s.positions.isdisjoint(occupied))
                occupied |= s.positions
            self.assertTrue(fleet.has_offense())

    def test_debug_enemy_layout_and_hit_near_sunk(self):
        for size in (4, 5, 6):
            enemy = make_debug_enemy_fleet(size)
            self.assertEqual(enemy.size, size)
            self.assertEqual(len(enemy.occupied()), sum(len(s.positions) for s in enemy.ships))
            self.assertEqual(enemy.attack((0, 0))[0], 'hit')
            self.assertEqual(enemy.attack((0, 1))[0], 'sunk')
            self.assertEqual(enemy.attack((1, 0))[0], 'near')
            self.assertEqual(enemy.attack((size - 1, size - 1))[0], 'near' if size in (4, 5) else 'miss')
            self.assertIn('S', ''.join(board_lines(enemy, own=True)))

    def test_debug_placement_rejects_impossible_three_carriers(self):
        impossible = Fleet([Carrier(), Carrier(), Carrier()], 4)
        self.assertFalse(try_place_all(impossible, random.Random(0)))
        possible = Fleet([AttackSubmarine(), StandardSubmarine(), Scout()], 4)
        self.assertTrue(try_place_all(possible, random.Random(2)))
        self.assertEqual(len(possible.occupied()), 6)
        self.assertEqual(sum(len(s.positions) for s in possible.ships), 6)
        with patch('builtins.input', side_effect=['4 4 4', '1 2 5']):
            with redirect_stdout(StringIO()):
                selected = choose_fleet(4)
        self.assertEqual([type(s) for s in selected], [AttackSubmarine, StandardSubmarine, Scout])

    def test_debug_mode_interactive_play_to_victory(self):
        # Fixed 4x4 target layout: A at (1,1),(1,2), S at (3,2..4), P at (4,1)
        inputs = ['1 2 5', '', '1', '1 1', '1', '1 2',
                  '1', '3 2', '1', '3 3', '1', '3 4', '1', '4 1']
        with patch('builtins.input', side_effect=inputs):
            with redirect_stdout(StringIO()) as output:
                result = run_game(random.Random(3), animations=False, debug_size=4)
        self.assertEqual(result, 'win')
        self.assertIn('DEBUG', output.getvalue())
        self.assertIn('撃沈！', output.getvalue())
        self.assertNotIn('敵の攻撃', output.getvalue())

    def test_auto_demo_finishes(self):
        # output is suppressed by the test command's redirect_stdout
        for seed in range(12):
            result = run_game(random.Random(seed), animations=False, auto_demo=True)
            self.assertIn(result, ('win', 'lose'))


if __name__ == '__main__':
    unittest.main()
