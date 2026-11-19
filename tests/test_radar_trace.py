"""レーダーの座標別表示と探知寿命を検証する。"""
import random
import sys
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from torpedo_game import Fleet, Scout, AttackSubmarine, board_lines, perform_player_action, show_boards

class RadarContactTests(unittest.TestCase):
    def make_fleets(self, size=9):
        scout, sub = Scout(), AttackSubmarine()
        player = Fleet([scout, sub], size)
        player.place(scout, 0, 0)
        player.place(sub, 2, 0)
        target = AttackSubmarine()
        enemy = Fleet([target], size)
        enemy.place(target, 4, 4)
        return player, enemy

    def action(self, key, player, enemy, shots, *inputs):
        with patch('builtins.input', side_effect=inputs):
            with redirect_stdout(StringIO()):
                return perform_player_action(key, player, enemy, shots, False)

    def test_scan_locates_contacts_without_exposing_ship_class(self):
        player, enemy = self.make_fleets()
        self.assertTrue(self.action('radar', player, enemy, {}, '5 5'))
        self.assertEqual(len(player.radar_contacts), 9)
        self.assertEqual(sum(c.detected for c in player.radar_contacts.values()), 2)
        rows = board_lines(enemy, radar_contacts=player.radar_contacts)
        self.assertIn('^', ''.join(rows))
        self.assertIn('-', ''.join(rows))
        self.assertNotIn('A', ''.join(rows))
        self.assertEqual(player.radar_contacts[(4, 4)].remaining, 2)
        with redirect_stdout(StringIO()) as output:
            show_boards(player, enemy, {}, {}, 2)
        self.assertIn('艦影反応 2マス', output.getvalue())
        self.assertIn('残り2行動', output.getvalue())

    def test_overlay_lasts_two_successful_actions(self):
        player, enemy = self.make_fleets()
        shots = {}
        self.action('radar', player, enemy, shots, '5 5')
        self.action('torpedo', player, enemy, shots, '5 5')
        self.assertEqual(player.radar_contacts[(4, 4)].remaining, 1)
        self.assertEqual(shots[(4, 4)], 'hit')
        self.assertIn('X', ''.join(board_lines(enemy, shots, radar_contacts=player.radar_contacts)))
        self.action('torpedo', player, enemy, shots, '6 5')
        self.assertEqual(player.radar_contacts, {})
        self.assertIn('X', ''.join(board_lines(enemy, shots)))

    def test_new_scan_keeps_nonoverlapping_old_cells_until_expired(self):
        player, enemy = self.make_fleets(6)
        shots = {}
        self.action('radar', player, enemy, shots, '1 1')
        self.assertEqual(len(player.radar_contacts), 4)
        self.action('radar', player, enemy, shots, '6 6')
        self.assertEqual(len(player.radar_contacts), 8)
        self.assertEqual(player.radar_contacts[(0, 0)].remaining, 1)
        self.assertEqual(player.radar_contacts[(5, 5)].remaining, 2)
        self.action('torpedo', player, enemy, shots, '3 3')
        self.assertNotIn((0, 0), player.radar_contacts)
        self.assertEqual(player.radar_contacts[(5, 5)].remaining, 1)

    def test_repeat_scan_refreshes_matching_cell(self):
        player, enemy = self.make_fleets()
        self.action('radar', player, enemy, {}, '5 5')
        self.action('radar', player, enemy, {}, '5 5')
        self.assertTrue(all(c.remaining == 2 for c in player.radar_contacts.values()))

    def test_cancelled_attack_does_not_age_contacts(self):
        player, enemy = self.make_fleets()
        shots = {(0, 0): 'miss'}
        self.action('radar', player, enemy, shots, '5 5')
        self.assertFalse(self.action('torpedo', player, enemy, shots, '1 1'))
        self.assertEqual(player.radar_contacts[(4, 4)].remaining, 2)

    def test_edge_scan_clipped_and_accurate(self):
        player, enemy = self.make_fleets(6)
        self.action('wide_radar', player, enemy, {}, '1 1')
        self.assertEqual(len(player.radar_contacts), 9)
        self.assertTrue(all(not c.detected for c in player.radar_contacts.values()))

    def test_scan_works_without_ammo_depletion(self):
        player, enemy = self.make_fleets()
        for _ in range(5):
            self.action('radar', player, enemy, {}, '5 5')
        self.assertIsNone(player.ammo['radar'])
        self.assertEqual(player.radar_contacts[(4, 4)].remaining, 2)

if __name__ == '__main__':
    unittest.main()
