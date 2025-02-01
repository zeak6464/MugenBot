import random
import time
from pathlib import Path
from typing import List, Dict, Optional
from enum import Enum
import math

class TournamentFormat(Enum):
    SINGLE_ELIMINATION = "Single Elimination"

class MugenTournament:
    def __init__(self, battle_manager, players: List[str], stage_pool: Optional[List[str]] = None):
        """
        Initialize tournament with battle manager and players
        
        Args:
            battle_manager: MugenBattleManager instance to handle matches
            players: List of character names to participate
            stage_pool: Optional list of stages to use (uses all enabled stages if None)
        """
        self.manager = battle_manager
        self.players = players
        self.stage_pool = stage_pool or list(self.manager.settings["enabled_stages"])
        self.current_battle = None
        self.format = TournamentFormat.SINGLE_ELIMINATION
        
        # Initialize brackets list
        self.brackets = []
        self.current_round = 0
        self.current_match_index = 0
        
        # Set up the tournament
        self._setup_single_elimination()

    def _setup_single_elimination(self):
        """Setup a single elimination bracket"""
        # Calculate number of byes needed
        bracket_size = 2 ** math.ceil(math.log2(len(self.players)))
        num_byes = bracket_size - len(self.players)
        
        # Create first round matches with byes
        first_round = self.players + ['BYE'] * num_byes
        random.shuffle(first_round)
        
        first_round_matches = []
        for i in range(0, len(first_round), 2):
            match = {
                'player1': first_round[i],
                'player2': first_round[i + 1],
                'winner': None,
                'loser': None,
                'stage': random.choice(self.stage_pool),
                'completed': False,
                'round': 0
            }
            # Auto-advance matches with BYE
            if match['player2'] == 'BYE':
                match['winner'] = match['player1']
                match['completed'] = True
            elif match['player1'] == 'BYE':
                match['winner'] = match['player2']
                match['completed'] = True
            
            first_round_matches.append(match)
        
        self.brackets = [first_round_matches]

    def advance_round(self):
        """Set up next round matches"""
        if not self.brackets or self.current_round >= len(self.brackets):
            return
            
        current_round = self.brackets[self.current_round]
        winners = []
        
        # Get winners from current round
        for match in current_round:
            if match['completed'] and match['winner']:
                winners.append(match['winner'])
        
        # Create next round matches
        if len(winners) >= 2:
            next_round = []
            for i in range(0, len(winners), 2):
                if i + 1 < len(winners):
                    match = {
                        'player1': winners[i],
                        'player2': winners[i + 1],
                        'winner': None,
                        'loser': None,
                        'stage': random.choice(self.stage_pool),
                        'completed': False,
                        'round': self.current_round + 1
                    }
                    next_round.append(match)
            
            if next_round:
                self.brackets.append(next_round)
                self.current_round += 1
                self.current_match_index = 0

    def get_bracket_display(self) -> str:
        """Get a text representation of the tournament bracket"""
        display = []
        
        # Show tournament type header
        display.append(f"Tournament Type: {self.format.value}")
        display.append("-" * 40)
        
        # Show rounds and matches
        for round_num, round_matches in enumerate(self.brackets):
            display.append(f"\nRound {round_num + 1}:")
            for match_num, match in enumerate(round_matches, 1):
                p1 = match['player1']
                p2 = match['player2']
                if match['completed']:
                    winner = match['winner']
                    display.append(f"Match {match_num}: {p1} vs {p2} -> {winner}")
                else:
                    display.append(f"Match {match_num}: {p1} vs {p2}")
        
        return "\n".join(display)

    def is_complete(self) -> bool:
        """Check if tournament is complete"""
        if not self.brackets:
            return False
        return len(self.brackets[-1]) == 1 and self.brackets[-1][0]['completed']

    def get_winner(self) -> Optional[str]:
        """Get the tournament winner if tournament is complete"""
        if self.is_complete():
            return self.brackets[-1][0]['winner']
        return None

    def start_next_match(self) -> Optional[Dict]:
        """Start the next match in the tournament"""
        try:
            if not self.brackets or self.current_round >= len(self.brackets):
                return None
            
            current_round = self.brackets[self.current_round]
            
            # Find next unplayed match
            while self.current_match_index < len(current_round):
                match = current_round[self.current_match_index]
                if not match['completed']:
                    # Prepare battle info
                    battle_info = {
                        'mode': 'single',
                        'p1': match['player1'],
                        'p2': match['player2'],
                        'stage': match['stage'],
                        'tournament_match': True,
                        'match_index': self.current_match_index,
                        'round': self.current_round
                    }
                    
                    self.current_battle = battle_info
                    self.manager.start_battle(battle_info)
                    return battle_info
                    
                self.current_match_index += 1
                
            # If we get here, current round is complete
            self.advance_round()
            self.current_match_index = 0
            return self.start_next_match() if not self.is_complete() else None
            
        except Exception as e:
            print(f"Error starting next match: {e}")
            return None

    def check_match_result(self) -> Optional[Dict]:
        """Check current match result"""
        if not self.current_battle:
            return None
        
        result = self.manager.check_battle_result()
        if result:
            # Process match result
            match_index = self.current_battle['match_index']
            current_round = self.brackets[self.current_round]
            match = current_round[match_index]
            
            match['winner'] = result['winner']
            match['loser'] = result['loser']
            match['completed'] = True
            
            # Clear current battle
            self.current_battle = None
            
            # Check if round is complete
            if all(m['completed'] for m in current_round):
                self.advance_round()
                self.current_match_index = 0
            else:
                self.current_match_index += 1
            
            return result
        
        return None

def create_tournament(battle_manager, num_players: int = 8) -> MugenTournament:
    """Create a new tournament with random selection of enabled characters"""
    enabled_chars = list(battle_manager.settings["enabled_characters"])
    if len(enabled_chars) < num_players:
        raise ValueError(f"Not enough enabled characters for {num_players}-player tournament")
        
    # Randomly select players
    players = random.sample(enabled_chars, num_players)
    
    # Create tournament
    return MugenTournament(battle_manager, players)

# Example usage with MugenBattleManager
if __name__ == "__main__":
    from random_ai_battles import MugenBattleManager
    
    # Initialize battle manager
    manager = MugenBattleManager()
    
    # Create tournament with 8 random characters
    tournament = create_tournament(manager, 8)
    
    print("Tournament Bracket:")
    print(tournament.get_bracket_display())
    
    # Main tournament loop
    while not tournament.is_complete():
        # Start next match
        match_info = tournament.start_next_match()
        if match_info:
            print(f"\nStarting match: {match_info['p1']} vs {match_info['p2']}")
            
            # Wait for match to complete
            while True:
                result = tournament.check_match_result()
                if result:
                    print(f"Winner: {result['winner']}")
                    break
                time.sleep(1)
    
    # Print final results
    print("\nTournament Complete!")
    print(f"Winner: {tournament.get_winner()}")
    print("\nFinal Bracket:")
    print(tournament.get_bracket_display()) 