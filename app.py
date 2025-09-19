from flask import Flask, render_template, request, jsonify, session
import json
import os
import random
from datetime import datetime
import uuid

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'your-secret-key-change-this')

# Add CORS headers to all responses
@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE')
    return response

# Single game instance
game = None

class SaatAathGame:
    def __init__(self, game_id=None):
        self.game_id = game_id or str(uuid.uuid4())
        self.suits = ['hearts', 'diamonds', 'clubs', 'spades']
        self.ranks = ['A', 'K', 'Q', 'J', '10', '9', '8', '7']
        self.suit_symbols = {
            'hearts': '♥',
            'diamonds': '♦',
            'clubs': '♣',
            'spades': '♠'
        }
        
        # Initialize game state
        self.state = {
            'game_id': self.game_id,
            'players': {
                '1': {'connected': False, 'session_id': None},
                '2': {'connected': False, 'session_id': None}
            },
            'trump': None,
            'deck': [],
            'player_cards': {
                '1': {'hand': [], 'table': [], 'tricks': 0},
                '2': {'hand': [], 'table': [], 'tricks': 0}
            },
            'current_trick_num': 1,
            'current_trick': [],
            'turn_player': 2,
            'game_phase': 'waiting_for_players',  # waiting_for_players, trump_selection, playing, finished
            'game_log': [],
            'winner': None,
            'created_at': datetime.now().isoformat(),
            'last_updated': datetime.now().isoformat()
        }
    
    def create_deck(self):
        deck = []
        for suit in self.suits:
            for rank in self.ranks:
                # Skip 2-6 and 7♣, 7♦
                if rank in ['2', '3', '4', '5', '6']:
                    continue
                if rank == '7' and suit in ['clubs', 'diamonds']:
                    continue
                deck.append({'suit': suit, 'rank': rank})
        
        return self.shuffle_array(deck)
    
    def shuffle_array(self, array):
        arr = array.copy()
        random.shuffle(arr)
        return arr
    
    def deal_cards(self):
        self.state['deck'] = self.create_deck()
        card_index = 0
        
        # Deal 5 cards to each player's hand
        for i in range(5):
            self.state['player_cards']['1']['hand'].append(self.state['deck'][card_index])
            card_index += 1
            self.state['player_cards']['2']['hand'].append(self.state['deck'][card_index])
            card_index += 1

        # Deal table cards (5 stacks each)
        for i in range(5):
            self.state['player_cards']['1']['table'].append({
                'face_down': self.state['deck'][card_index],
                'face_up': self.state['deck'][card_index + 1]
            })
            card_index += 2
            self.state['player_cards']['2']['table'].append({
                'face_down': self.state['deck'][card_index],
                'face_up': self.state['deck'][card_index + 1]
            })
            card_index += 2
    
    def connect_player(self, player_num, session_id):
        print(f"Connecting player {player_num} with session {session_id}")
        print(f"Current players state: {self.state.get('players', {})}")

        if player_num in [1, 2]:
            # Convert to string keys for JSON compatibility
            player_key = str(player_num)

            # Ensure players dict exists and has the right structure
            if 'players' not in self.state:
                self.state['players'] = {
                    '1': {'connected': False, 'session_id': None},
                    '2': {'connected': False, 'session_id': None}
                }

            if player_key not in self.state['players']:
                self.state['players'][player_key] = {'connected': False, 'session_id': None}

            # Auto-kick previous player if someone new joins
            self.state['players'][player_key]['connected'] = True
            self.state['players'][player_key]['session_id'] = session_id

            # Deal cards immediately when first player connects
            if self.state['game_phase'] == 'waiting_for_players':
                self.deal_cards()
                self.state['game_phase'] = 'trump_selection'
                if self.state['players']['1']['connected'] and self.state['players']['2']['connected']:
                    self.add_log('Both players connected. Player 2 (non-dealer) choose trump suit.')
                else:
                    self.add_log('Player connected. Waiting for opponent. Player 2 will choose trump suit.')

            self.save_state()
            return True
        return False
    
    def choose_trump(self, suit, player_num):
        if (player_num == 2 and
            self.state['game_phase'] == 'trump_selection' and
            suit in self.suits):
            self.state['trump'] = suit
            self.state['game_phase'] = 'playing'
            self.add_log(f'Trump suit: {self.suit_symbols[suit]} {suit.title()}')
            self.add_log('Player 2 (non-dealer) leads first trick.')
            self.save_state()
            return True
        return False
    
    def get_card_value(self, card):
        # 7♥ highest trump, 7♠ second highest trump
        if card['rank'] == '7' and card['suit'] == 'hearts':
            return 1000
        if card['rank'] == '7' and card['suit'] == 'spades':
            return 999
        
        # Trump suit cards
        if card['suit'] == self.state['trump']:
            trump_values = {'A': 14, 'K': 13, 'Q': 12, 'J': 11, '10': 10, '9': 9, '8': 8}
            return trump_values[card['rank']] + 100
        
        # Regular cards
        values = {'A': 14, 'K': 13, 'Q': 12, 'J': 11, '10': 10, '9': 9, '8': 8, '7': 7}
        return values[card['rank']]
    
    def is_card_trump(self, card):
        return (card['suit'] == self.state['trump'] or 
                (card['rank'] == '7' and card['suit'] == 'hearts') or 
                (card['rank'] == '7' and card['suit'] == 'spades'))
    
    def get_playable_cards(self, player_num):
        player = self.state['player_cards'][str(player_num)]
        playable = player['hand'].copy()
        
        # Add face-up cards from table
        for stack in player['table']:
            if stack['face_up']:
                playable.append(stack['face_up'])
        
        return playable
    
    def is_valid_play(self, card, player_num):
        if len(self.state['current_trick']) == 0:
            return True
        
        led_card = self.state['current_trick'][0]['card']
        playable_cards = self.get_playable_cards(player_num)
        
        # Special handling for 7♥ and 7♠ when led
        if ((led_card['rank'] == '7' and led_card['suit'] == 'hearts') or 
            (led_card['rank'] == '7' and led_card['suit'] == 'spades')):
            has_trump = any(self.is_card_trump(c) for c in playable_cards)
            if has_trump and not self.is_card_trump(card):
                return False
            return True
        
        # Trump card led
        if self.is_card_trump(led_card):
            has_trump = any(self.is_card_trump(c) for c in playable_cards)
            if has_trump and not self.is_card_trump(card):
                return False
            return True
        
        # Regular suit led
        has_same_suit = any(c['suit'] == led_card['suit'] and not self.is_card_trump(c) 
                           for c in playable_cards)
        if has_same_suit and (card['suit'] != led_card['suit'] or self.is_card_trump(card)):
            return False
        
        return True
    
    def play_card(self, card, player_num):
        if (player_num != self.state['turn_player'] or
            self.state['game_phase'] != 'playing' or
            not self.is_valid_play(card, player_num)):
            return False

        player = self.state['player_cards'][str(player_num)]
        
        # Remove card from hand or table
        card_removed = False
        for i, hand_card in enumerate(player['hand']):
            if (hand_card['suit'] == card['suit'] and 
                hand_card['rank'] == card['rank']):
                player['hand'].pop(i)
                card_removed = True
                break
        
        if not card_removed:
            # Remove from table and reveal face-down card
            for stack in player['table']:
                if (stack['face_up'] and 
                    stack['face_up']['suit'] == card['suit'] and 
                    stack['face_up']['rank'] == card['rank']):
                    stack['face_up'] = stack['face_down']
                    stack['face_down'] = None
                    card_removed = True
                    break
        
        if not card_removed:
            return False
        
        self.state['current_trick'].append({'card': card, 'player': player_num})
        
        if len(self.state['current_trick']) == 2:
            self.evaluate_trick()
        else:
            self.state['turn_player'] = 2 if player_num == 1 else 1
        
        self.save_state()
        return True
    
    def evaluate_trick(self):
        trick = self.state['current_trick']
        card1 = trick[0]['card']
        card2 = trick[1]['card']
        
        # Determine winner
        if (card1['suit'] == card2['suit'] or 
            (self.is_card_trump(card1) and self.is_card_trump(card2))):
            winner = trick[0]['player'] if self.get_card_value(card1) > self.get_card_value(card2) else trick[1]['player']
        elif self.is_card_trump(card1) and not self.is_card_trump(card2):
            winner = trick[0]['player']
        elif not self.is_card_trump(card1) and self.is_card_trump(card2):
            winner = trick[1]['player']
        else:
            winner = trick[0]['player']  # First card wins if different suits, no trump
        
        self.state['player_cards'][str(winner)]['tricks'] += 1
        self.state['turn_player'] = winner
        
        card1_str = f"{card1['rank']}{self.suit_symbols[card1['suit']]}"
        card2_str = f"{card2['rank']}{self.suit_symbols[card2['suit']]}"
        self.add_log(f"Trick {self.state['current_trick_num']}: {card1_str} vs {card2_str} - Player {winner} wins")
        
        self.state['current_trick'] = []
        self.state['current_trick_num'] += 1
        
        if self.state['current_trick_num'] > 15:
            self.end_game()
    
    def end_game(self):
        self.state['game_phase'] = 'finished'
        
        p1_tricks = self.state['player_cards']['1']['tricks']
        p2_tricks = self.state['player_cards']['2']['tricks']
        
        # Player 1 (non-dealer) needs 8+ tricks, Player 2 (dealer) needs 7+ tricks
        if p1_tricks >= 8:  # Non-dealer wins with 8+
            self.state['winner'] = 1
            message = f"Player 1 (Non-dealer) wins with {p1_tricks} tricks!"
        elif p2_tricks >= 7:  # Dealer wins with 7+
            self.state['winner'] = 2
            message = f"Player 2 (Dealer) wins with {p2_tricks} tricks!"
        elif p1_tricks == 8 and p2_tricks == 7:
            # This is actually impossible since they sum to 15, but keeping for clarity
            message = f"Tie game! Player 1: {p1_tricks}, Player 2: {p2_tricks}"
        else:
            message = f"Tie game! Player 1: {p1_tricks}, Player 2: {p2_tricks}"
        
        self.add_log(message)
        self.save_state()
    
    def add_log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.state['game_log'].append(f"[{timestamp}] {message}")
    
    def save_state(self):
        self.state['last_updated'] = datetime.now().isoformat()
        # No need to save to file since we only have one game
    
    def reset_game(self):
        # Keep game_id and connected players
        players = self.state['players']
        self.state = {
            'game_id': self.game_id,
            'players': players,
            'trump': None,
            'deck': [],
            'player_cards': {
                '1': {'hand': [], 'table': [], 'tricks': 0},
                '2': {'hand': [], 'table': [], 'tricks': 0}
            },
            'current_trick_num': 1,
            'current_trick': [],
            'turn_player': 2,
            'game_phase': 'trump_selection',
            'game_log': [],
            'winner': None,
            'created_at': datetime.now().isoformat(),
            'last_updated': datetime.now().isoformat()
        }
        
        if players['1']['connected'] and players['2']['connected']:
            self.deal_cards()
            self.add_log('New game started. Player 2 (non-dealer) choose trump suit.')

        self.save_state()

def get_or_create_game():
    global game
    if game is None:
        game = SaatAathGame()
    return game

@app.route('/')
def index():
    current_game = get_or_create_game()
    return render_template('index.html', game_id=current_game.game_id)

@app.route('/api/connect', methods=['POST'])
def connect():
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No JSON data provided'}), 400

        game_id = data.get('game_id')
        player_num = data.get('player_num')

        print(f"Connect request: game_id={game_id}, player_num={player_num}")

        if not game_id or player_num not in [1, 2]:
            return jsonify({'error': 'Invalid game_id or player_num'}), 400

        current_game = get_or_create_game()
        session_id = session.get('session_id', str(uuid.uuid4()))
        session['session_id'] = session_id
        session['game_id'] = game_id
        session['player_num'] = player_num

        success = current_game.connect_player(player_num, session_id)

        if success:
            return jsonify({
                'success': True,
                'game_state': current_game.state,
                'session_id': session_id
            })
        else:
            return jsonify({'error': 'Could not connect to game'}), 400
    except Exception as e:
        print(f"Error in connect: {e}")
        return jsonify({'error': 'Server error'}), 500

@app.route('/api/game_state')
def game_state():
    current_game = get_or_create_game()
    return jsonify(current_game.state)

@app.route('/api/choose_trump', methods=['POST'])
def choose_trump():
    data = request.get_json()
    suit = data.get('suit')
    
    game_id = session.get('game_id')
    player_num = session.get('player_num')
    
    if not game_id or player_num != 2:
        return jsonify({'error': 'Unauthorized or invalid session'}), 400
    
    current_game = get_or_create_game()
    success = current_game.choose_trump(suit, player_num)

    if success:
        return jsonify({'success': True, 'game_state': current_game.state})
    else:
        return jsonify({'error': 'Could not choose trump'}), 400

@app.route('/api/play_card', methods=['POST'])
def play_card():
    data = request.get_json()
    card = data.get('card')
    
    game_id = session.get('game_id')
    player_num = session.get('player_num')
    
    if not game_id or not player_num:
        return jsonify({'error': 'Invalid session'}), 400
    
    current_game = get_or_create_game()
    success = current_game.play_card(card, player_num)

    if success:
        return jsonify({'success': True, 'game_state': current_game.state})
    else:
        return jsonify({'error': 'Invalid play'}), 400

@app.route('/api/reset_game', methods=['POST'])
def reset_game():
    current_game = get_or_create_game()
    current_game.reset_game()

    return jsonify({'success': True, 'game_state': current_game.state})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5090))
    app.run(host='0.0.0.0', port=port, debug=True)