import hashlib
import json
import math
import os
import random
import struct
import datetime
import wave

import kivy
import kivy.app
import kivy.uix.screenmanager
import kivy.uix.boxlayout
import kivy.uix.gridlayout
import kivy.uix.button
import kivy.uix.label
import kivy.uix.popup
import kivy.clock
import kivy.properties
import kivy.core.window
import kivy.metrics
import kivy.animation
import kivy.uix.progressbar
import kivy.uix.widget
import kivy.uix.scrollview
import kivy.uix.textinput
from kivy.graphics import Color, Rectangle, RoundedRectangle, Line, Ellipse
from kivy.core.text import LabelBase

# Register a modern font when available; otherwise keep Kivy default.
try:
    LabelBase.register(name='Roboto', fn_regular='Roboto-Regular.ttf')
except OSError:
    pass

# Set window size
kivy.core.window.Window.size = (400, 700)
kivy.core.window.Window.clearcolor = (1.0, 0.93, 0.18, 1)


def draw_comic_background(widget, colors=None):
    """Bright comic yellow with subtle halftone dots (and optional night ink variant)."""
    colors = colors or {}
    widget.canvas.before.clear()
    x, y = widget.pos
    w, h = widget.size
    night = colors.get('comic_night', False)
    with widget.canvas.before:
        if night:
            Color(0.11, 0.11, 0.13, 1)
            Rectangle(pos=(x, y), size=(w, h))
            Color(1.0, 0.88, 0.12, 0.14)
        else:
            Color(1.0, 0.93, 0.20, 1)
            Rectangle(pos=(x, y), size=(w, h))
            Color(1.0, 0.82, 0.05, 0.28)
        step = max(36.0, min(w, h) / 14)
        xi = x
        row = 0
        while xi < x + w + step:
            yi = y + (step * 0.45 if row % 2 else 0)
            while yi < y + h + step:
                d = step * 0.2
                Ellipse(pos=(xi, yi), size=(d, d))
                yi += step
            xi += step
            row += 1


QUIZ_QUESTIONS_PER_ROUND = 10

_APP_DIR = os.path.dirname(os.path.abspath(__file__))
QUESTIONS_JSON = os.path.join(_APP_DIR, 'questions.json')
LEADERBOARD_JSON = os.path.join(_APP_DIR, 'leaderboard.json')
SETTINGS_JSON = os.path.join(_APP_DIR, 'game_settings.json')


def _write_tone_wav(path, freq_hz, duration_ms=120, sample_rate=44100, volume=0.25):
    """Write a short mono 16-bit WAV tone (used when sound files are not bundled)."""
    n_samples = max(1, int(sample_rate * duration_ms / 1000))
    with wave.open(path, 'w') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        chunks = []
        for i in range(n_samples):
            t = i / sample_rate
            env = math.sin(math.pi * i / max(1, n_samples - 1)) if n_samples > 1 else 1.0
            sample = volume * env * math.sin(2 * math.pi * freq_hz * t)
            iv = int(max(-1.0, min(1.0, sample)) * 32767)
            chunks.append(struct.pack('<h', iv))
        wf.writeframes(b''.join(chunks))


def ensure_feedback_sound_files():
    """Create default correct/wrong WAVs if missing so Kivy SoundLoader can play feedback."""
    base = os.path.join(_APP_DIR, 'assets', 'sounds')
    try:
        os.makedirs(base, exist_ok=True)
    except OSError:
        return
    correct_path = os.path.join(base, 'correct.wav')
    wrong_path = os.path.join(base, 'wrong.wav')
    if not os.path.isfile(correct_path):
        try:
            _write_tone_wav(correct_path, freq_hz=880, duration_ms=90, volume=0.28)
        except OSError:
            pass
    if not os.path.isfile(wrong_path):
        try:
            _write_tone_wav(wrong_path, freq_hz=220, duration_ms=140, volume=0.22)
        except OSError:
            pass


_DAILY_CATEGORIES_ORDER = [
    'General Knowledge', 'Science', 'Technology', 'Movies & Entertainment', 'Sports',
    'Mathematics', 'Geography', 'Philippine Trivia', 'Anime', 'Riddles & Brain Teasers',
]


def _settings_default():
    return {'sound_enabled': True, 'high_contrast': False}


def load_app_settings():
    try:
        with open(SETTINGS_JSON, 'r', encoding='utf-8') as f:
            data = json.load(f)
            base = _settings_default()
            base.update(data)
            return base
    except (OSError, json.JSONDecodeError):
        return _settings_default()


def save_app_settings(settings):
    try:
        with open(SETTINGS_JSON, 'w', encoding='utf-8') as f:
            json.dump(settings, f, indent=2)
    except OSError:
        pass


def daily_spotlight_category():
    """Deterministic 'daily challenge' topic from local date."""
    d = datetime.date.today().isoformat()
    h = int(hashlib.sha256(d.encode()).hexdigest(), 16)
    return _DAILY_CATEGORIES_ORDER[h % len(_DAILY_CATEGORIES_ORDER)]


class LeaderboardStore:
    """Simple local JSON leaderboard (top scores)."""

    def __init__(self, path=LEADERBOARD_JSON, max_entries=50):
        self.path = path
        self.max_entries = max_entries

    def load(self):
        try:
            with open(self.path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return []

    def add_entry(self, name, score, total, category, difficulty, streak=0):
        rows = self.load()
        rows.append({
            'date': datetime.datetime.now().isoformat(timespec='seconds'),
            'name': (name or 'Player')[:24],
            'score': int(score),
            'total': int(total),
            'category': category,
            'difficulty': difficulty,
            'streak': int(streak),
        })
        rows.sort(key=lambda r: (r.get('score', 0), r.get('streak', 0)), reverse=True)
        rows = rows[: self.max_entries]
        try:
            with open(self.path, 'w', encoding='utf-8') as f:
                json.dump(rows, f, indent=2, ensure_ascii=False)
        except OSError:
            pass
        return rows


# ============ SIMPLE FILE-BASED USER MANAGEMENT ============
class UserManager:
    """Simple user management with file persistence"""

    def __init__(self):
        self.users_file = 'users_data.txt'
        self.save_file = 'quiz_save.txt'
        self.users = {}
        self.current_user = None
        self.load_users()

    def load_users(self):
        """Load users from text file"""
        try:
            with open(self.users_file, 'r') as f:
                lines = f.readlines()
                i = 0
                while i < len(lines):
                    line = lines[i].strip()
                    if line.startswith('[USER]'):
                        username = lines[i + 1].strip()
                        password = lines[i + 2].strip()
                        total_games = int(lines[i + 3].strip())
                        total_score = int(lines[i + 4].strip())
                        best_score = int(lines[i + 5].strip())

                        category_stats = {}
                        num_categories = int(lines[i + 6].strip())
                        i += 7
                        for _ in range(num_categories):
                            cat_name = lines[i].strip()
                            cat_played = int(lines[i + 1].strip())
                            cat_best = int(lines[i + 2].strip())
                            cat_total = int(lines[i + 3].strip())
                            category_stats[cat_name] = {
                                'played': cat_played,
                                'best': cat_best,
                                'total_score': cat_total
                            }
                            i += 4

                        easy_played = int(lines[i].strip())
                        easy_best = int(lines[i + 1].strip())
                        medium_played = int(lines[i + 2].strip())
                        medium_best = int(lines[i + 3].strip())
                        hard_played = int(lines[i + 4].strip())
                        hard_best = int(lines[i + 5].strip())
                        i += 6

                        num_achievements = int(lines[i].strip())
                        achievements = []
                        i += 1
                        for _ in range(num_achievements):
                            achievements.append(lines[i].strip())
                            i += 1

                        num_recent = int(lines[i].strip())
                        recent_games = []
                        i += 1
                        for _ in range(num_recent):
                            game_category = lines[i].strip()
                            game_difficulty = lines[i + 1].strip()
                            game_score = int(lines[i + 2].strip())
                            game_total = int(lines[i + 3].strip())
                            game_time = int(lines[i + 4].strip())
                            recent_games.append({
                                'category': game_category,
                                'difficulty': game_difficulty,
                                'score': game_score,
                                'total': game_total,
                                'time': game_time
                            })
                            i += 5

                        self.users[username] = {
                            'password': password,
                            'stats': {
                                'total_games': total_games,
                                'total_score': total_score,
                                'best_score': best_score,
                                'games_by_category': category_stats,
                                'games_by_difficulty': {
                                    'easy': {'played': easy_played, 'best': easy_best},
                                    'medium': {'played': medium_played, 'best': medium_best},
                                    'hard': {'played': hard_played, 'best': hard_best}
                                },
                                'achievements': achievements,
                                'recent_games': recent_games
                            }
                        }
                    else:
                        i += 1
        except FileNotFoundError:
            self.users['guest'] = {
                'password': '',
                'stats': {
                    'total_games': 0,
                    'total_score': 0,
                    'best_score': 0,
                    'games_by_category': {},
                    'games_by_difficulty': {
                        'easy': {'played': 0, 'best': 0},
                        'medium': {'played': 0, 'best': 0},
                        'hard': {'played': 0, 'best': 0}
                    },
                    'achievements': [],
                    'recent_games': []
                }
            }
        except Exception as e:
            print(f"Error loading users: {e}")
            self.users['guest'] = {
                'password': '',
                'stats': {
                    'total_games': 0,
                    'total_score': 0,
                    'best_score': 0,
                    'games_by_category': {},
                    'games_by_difficulty': {
                        'easy': {'played': 0, 'best': 0},
                        'medium': {'played': 0, 'best': 0},
                        'hard': {'played': 0, 'best': 0}
                    },
                    'achievements': [],
                    'recent_games': []
                }
            }

    def save_users(self):
        """Save users to text file"""
        try:
            with open(self.users_file, 'w') as f:
                for username, data in self.users.items():
                    if username == 'guest':
                        continue
                    stats = data['stats']

                    f.write("[USER]\n")
                    f.write(f"{username}\n")
                    f.write(f"{data['password']}\n")
                    f.write(f"{stats['total_games']}\n")
                    f.write(f"{stats['total_score']}\n")
                    f.write(f"{stats['best_score']}\n")
                    f.write(f"{len(stats['games_by_category'])}\n")
                    for cat_name, cat_stats in stats['games_by_category'].items():
                        f.write(f"{cat_name}\n")
                        f.write(f"{cat_stats['played']}\n")
                        f.write(f"{cat_stats['best']}\n")
                        f.write(f"{cat_stats['total_score']}\n")
                    f.write(f"{stats['games_by_difficulty']['easy']['played']}\n")
                    f.write(f"{stats['games_by_difficulty']['easy']['best']}\n")
                    f.write(f"{stats['games_by_difficulty']['medium']['played']}\n")
                    f.write(f"{stats['games_by_difficulty']['medium']['best']}\n")
                    f.write(f"{stats['games_by_difficulty']['hard']['played']}\n")
                    f.write(f"{stats['games_by_difficulty']['hard']['best']}\n")
                    f.write(f"{len(stats['achievements'])}\n")
                    for ach in stats['achievements']:
                        f.write(f"{ach}\n")
                    recent = stats.get('recent_games', [])
                    f.write(f"{len(recent)}\n")
                    for game in recent[-10:]:
                        f.write(f"{game['category']}\n")
                        f.write(f"{game['difficulty']}\n")
                        f.write(f"{game['score']}\n")
                        f.write(f"{game['total']}\n")
                        f.write(f"{game['time']}\n")
            return True
        except Exception as e:
            print(f"Error saving users: {e}")
            return False

    def save_quiz_state(self, category, difficulty, question_num, score, time_left, questions_answered,
                        seed=0, streak=0, max_streak=0):
        try:
            with open(self.save_file, 'w') as f:
                f.write(f"[SAVE]\n")
                f.write(f"{self.current_user if self.current_user else 'guest'}\n")
                f.write(f"{category}\n")
                f.write(f"{difficulty}\n")
                f.write(f"{question_num}\n")
                f.write(f"{score}\n")
                f.write(f"{time_left}\n")
                f.write(f"{questions_answered}\n")
                f.write(f"{seed}\n")
                f.write(f"{streak}\n")
                f.write(f"{max_streak}\n")
            return True
        except Exception:
            return False

    def load_quiz_state(self):
        try:
            with open(self.save_file, 'r') as f:
                lines = f.readlines()
                if lines[0].strip() == '[SAVE]':
                    saved_user = lines[1].strip()
                    if saved_user == self.current_user or (saved_user == 'guest' and not self.current_user):
                        base = {
                            'category': lines[2].strip(),
                            'difficulty': lines[3].strip(),
                            'question_num': int(lines[4].strip()),
                            'score': int(lines[5].strip()),
                            'time_left': int(lines[6].strip()),
                            'questions_answered': int(lines[7].strip()),
                            'seed': 0,
                            'streak': 0,
                            'max_streak': 0,
                        }
                        if len(lines) > 8:
                            try:
                                base['seed'] = int(lines[8].strip())
                            except ValueError:
                                pass
                        if len(lines) > 9:
                            try:
                                base['streak'] = int(lines[9].strip())
                            except ValueError:
                                pass
                        if len(lines) > 10:
                            try:
                                base['max_streak'] = int(lines[10].strip())
                            except ValueError:
                                pass
                        return base
            return None
        except Exception:
            return None

    def clear_save(self):
        try:
            with open(self.save_file, 'w') as f:
                f.write("")
            return True
        except:
            return False

    def register(self, username, password):
        if username in self.users:
            return False, "Username already exists!"
        if len(username) < 3:
            return False, "Username must be at least 3 characters!"
        if len(password) < 4:
            return False, "Password must be at least 4 characters!"

        self.users[username] = {
            'password': password,
            'stats': {
                'total_games': 0,
                'total_score': 0,
                'best_score': 0,
                'games_by_category': {},
                'games_by_difficulty': {
                    'easy': {'played': 0, 'best': 0},
                    'medium': {'played': 0, 'best': 0},
                    'hard': {'played': 0, 'best': 0}
                },
                'achievements': [],
                'recent_games': []
            }
        }
        self.save_users()
        return True, "Registration successful!"

    def login(self, username, password):
        if username not in self.users:
            return False, "Username not found!"
        if self.users[username]['password'] != password:
            return False, "Incorrect password!"
        self.current_user = username
        return True, f"Welcome back, {username}!"

    def logout(self):
        self.current_user = None
        return True, "Logged out!"

    def is_logged_in(self):
        return self.current_user is not None and self.current_user != "guest"

    def get_current_user(self):
        return self.current_user

    def update_stats(self, score, total_questions, category, difficulty, time_taken):
        if not self.current_user or self.current_user == "guest":
            return False

        percentage = (score / total_questions) * 100

        self.users[self.current_user]['stats']['total_games'] += 1
        self.users[self.current_user]['stats']['total_score'] += score

        if score > self.users[self.current_user]['stats']['best_score']:
            self.users[self.current_user]['stats']['best_score'] = score

        if category not in self.users[self.current_user]['stats']['games_by_category']:
            self.users[self.current_user]['stats']['games_by_category'][category] = {
                'played': 0, 'best': 0, 'total_score': 0
            }

        cat_stats = self.users[self.current_user]['stats']['games_by_category'][category]
        cat_stats['played'] += 1
        cat_stats['total_score'] += score
        if score > cat_stats['best']:
            cat_stats['best'] = score

        diff_stats = self.users[self.current_user]['stats']['games_by_difficulty'][difficulty]
        diff_stats['played'] += 1
        if score > diff_stats['best']:
            diff_stats['best'] = score

        recent_game = {
            'category': category,
            'difficulty': difficulty,
            'score': score,
            'total': total_questions,
            'time': time_taken
        }

        if 'recent_games' not in self.users[self.current_user]['stats']:
            self.users[self.current_user]['stats']['recent_games'] = []

        recent_games = self.users[self.current_user]['stats']['recent_games']
        recent_games.insert(0, recent_game)
        self.users[self.current_user]['stats']['recent_games'] = recent_games[:15]

        achievements = self.users[self.current_user]['stats']['achievements']

        if score == total_questions and 'PERFECT_SCORE' not in achievements:
            achievements.append('PERFECT_SCORE')
        if percentage >= 80 and 'EXCELLENT' not in achievements:
            achievements.append('EXCELLENT')
        if score == total_questions and difficulty == 'easy' and 'EASY_MASTER' not in achievements:
            achievements.append('EASY_MASTER')
        if score == total_questions and difficulty == 'medium' and 'MEDIUM_MASTER' not in achievements:
            achievements.append('MEDIUM_MASTER')
        if score == total_questions and difficulty == 'hard' and 'HARD_MASTER' not in achievements:
            achievements.append('HARD_MASTER')

        self.save_users()
        return True

    def get_user_stats(self):
        if not self.current_user or self.current_user == "guest":
            return None
        return self.users[self.current_user]['stats']


# ============ QUESTION DATA ============
class Question:
    def __init__(self, question, options, correct_answer, difficulty='medium', explanation=''):
        self.question = question
        self.options = options
        self.correct_answer = correct_answer
        self.difficulty = difficulty
        self.explanation = explanation or ''


def _mc(qtext, correct, w1, w2, w3, explanation='', difficulty='medium'):
    """Build a 4-option multiple-choice question (correct answer must match button text exactly)."""
    return Question(qtext, [w1, w2, w3, correct], correct, difficulty, explanation)


# --- Category pools: 10 questions each (same pool used for easy / medium / hard; timer still varies) ---
_POOL_GENERAL = [
    _mc("What is the capital city of Japan?", "Tokyo", "Kyoto", "Osaka", "Seoul"),
    _mc("Which planet is known as the Red Planet?", "Mars", "Venus", "Jupiter", "Saturn"),
    _mc("How many continents are there in the world?", "7", "5", "6", "8"),
    _mc("What is the largest ocean on Earth?", "Pacific Ocean", "Atlantic Ocean", "Indian Ocean", "Arctic Ocean"),
    _mc("Who invented the telephone?", "Alexander Graham Bell", "Thomas Edison", "Nikola Tesla", "Samuel Morse"),
    _mc("What is the national animal of the Philippines?", "Carabao", "Philippine Eagle", "Tamaraw", "Maya"),
    _mc("Which country is known as the Land of the Rising Sun?", "Japan", "China", "South Korea", "Thailand"),
    _mc("What is the boiling point of water in Celsius?", "100°C", "90°C", "99°C", "212°C"),
    _mc("Which language has the most native speakers in the world?", "Mandarin Chinese", "English", "Spanish", "Hindi"),
    _mc("What is the smallest country in the world?", "Vatican City", "Monaco", "Malta", "San Marino"),
]
_POOL_SCIENCE = [
    _mc("What gas do plants absorb from the atmosphere?", "Carbon Dioxide", "Oxygen", "Nitrogen", "Hydrogen"),
    _mc("What is the chemical symbol for gold?", "Au", "Ag", "Fe", "Pb"),
    _mc("Which organ pumps blood throughout the body?", "Heart", "Liver", "Brain", "Lungs"),
    _mc("What is the center of an atom called?", "Nucleus", "Electron", "Neutron only", "Cell wall"),
    _mc("What force keeps planets in orbit around the sun?", "Gravity", "Magnetism", "Friction", "Inertia"),
    _mc("How many bones are in the adult human body?", "206", "200", "212", "180"),
    _mc("Which vitamin is produced when exposed to sunlight?", "Vitamin D", "Vitamin A", "Vitamin B12", "Vitamin C"),
    _mc("What is H2O commonly known as?", "Water", "Hydrogen", "Salt water", "Ice only"),
    _mc("Which planet has the most moons?", "Saturn", "Jupiter", "Uranus", "Neptune"),
    _mc("What is the fastest land animal?", "Cheetah", "Lion", "Leopard", "Horse"),
]
_POOL_TECH = [
    _mc("What does CPU stand for?", "Central Processing Unit", "Computer Personal Unit", "Core Processing Utility", "Central Program Unit"),
    _mc("Which company created the iPhone?", "Apple", "Samsung", "Google", "Sony"),
    _mc("What does HTML stand for?", "HyperText Markup Language", "High Tech Modern Language", "Hyperlink Text Mode Language", "Home Tool Markup Language"),
    _mc("Which social media platform is known for short videos?", "TikTok", "Instagram", "LinkedIn", "Reddit"),
    _mc("What is the brain of the computer?", "CPU", "RAM", "Hard drive", "Monitor"),
    _mc("Which programming language is commonly used for web styling?", "CSS", "HTML", "Python", "SQL"),
    _mc("What does Wi-Fi allow devices to do?", "Connect to the internet wirelessly", "Print without cables", "Charge faster", "Store unlimited data"),
    _mc("Which company developed Windows OS?", "Microsoft", "Apple", "IBM", "Intel"),
    _mc("What is the most popular search engine?", "Google", "Bing", "Yahoo", "DuckDuckGo"),
    _mc("What does USB stand for?", "Universal Serial Bus", "United System Bus", "Ultra Speed Bandwidth", "Universal Signal Bridge"),
]
_POOL_MOVIES = [
    _mc("Who is the main character in Harry Potter?", "Harry Potter", "Ron Weasley", "Hermione Granger", "Draco Malfoy"),
    _mc("Which movie features the character Iron Man?", "Iron Man", "Thor", "Black Panther", "Doctor Strange"),
    _mc("What is the name of Simba's father in The Lion King?", "Mufasa", "Scar", "Zazu", "Timon"),
    _mc("Which movie features blue aliens called Na'vi?", "Avatar", "Dune", "Star Trek", "Guardians of the Galaxy"),
    _mc("Who played Jack in Titanic?", "Leonardo DiCaprio", "Brad Pitt", "Tom Cruise", "Johnny Depp"),
    _mc("Which Disney princess lost her glass slipper?", "Cinderella", "Belle", "Ariel", "Snow White"),
    _mc("What is one of the highest-grossing movies of all time?", "Avatar", "Casablanca", "The Room", "Citizen Kane"),
    _mc("Which superhero is known as the Dark Knight?", "Batman", "Superman", "Spider-Man", "Flash"),
    _mc("What school does Harry Potter attend?", "Hogwarts", "Beauxbatons", "Ilvermorny", "Durmstrang"),
    _mc("Which movie franchise features lightsabers?", "Star Wars", "Star Trek", "Harry Potter", "Fast & Furious"),
]
_POOL_SPORTS = [
    _mc("How many players are on a basketball team on the court?", "5", "4", "6", "7"),
    _mc("Which country won the FIFA World Cup in 2022?", "Argentina", "France", "Brazil", "Germany"),
    _mc("What sport uses a racket and shuttlecock?", "Badminton", "Tennis", "Squash", "Table tennis"),
    _mc("How many rings are on the Olympic flag?", "5", "4", "6", "7"),
    _mc("What sport is Michael Jordan famous for?", "Basketball", "Baseball", "Golf", "Soccer"),
    _mc("Which sport is often called the world's most popular sport?", "Soccer", "Cricket", "American football", "Ice hockey"),
    _mc("What is the national sport of the Philippines?", "Arnis", "Basketball", "Boxing", "Volleyball"),
    _mc("Which sport uses pins and a bowling ball?", "Bowling", "Golf", "Curling", "Darts"),
    _mc("How many points is a touchdown worth in American football?", "6", "3", "7", "2"),
    _mc("What sport does Novak Djokovic play?", "Tennis", "Badminton", "Squash", "Table tennis"),
]
_POOL_MATH = [
    _mc("What is 9 x 8?", "72", "64", "81", "56"),
    _mc("What is the square root of 81?", "9", "8", "10", "7"),
    _mc("What is 15 + 27?", "42", "41", "43", "52"),
    _mc("What is Pi rounded to two decimal places?", "3.14", "3.41", "3.15", "2.71"),
    _mc("What is 100 divided by 4?", "25", "20", "24", "30"),
    _mc("What is 12 squared?", "144", "132", "156", "169"),
    _mc("What is half of 250?", "125", "100", "150", "200"),
    _mc("What is 7 cubed?", "343", "333", "351", "329"),
    _mc("Solve: 18 - 9 + 6", "15", "12", "21", "18"),
    _mc("What is the perimeter formula for a rectangle?", "2(length + width)", "length × width", "length + width", "4 × length"),
]
_POOL_GEO = [
    _mc("What is the largest country in the world?", "Russia", "Canada", "China", "United States"),
    _mc("Which continent is Egypt located in?", "Africa", "Asia", "Europe", "Australia"),
    _mc("What is the longest river in the world?", "Nile River", "Amazon River", "Yangtze River", "Mississippi River"),
    _mc("Which country has the largest population?", "India", "China", "United States", "Indonesia"),
    _mc("What is the capital of France?", "Paris", "Lyon", "Marseille", "Nice"),
    _mc("Which desert is the largest hot desert in the world?", "Sahara Desert", "Gobi Desert", "Arabian Desert", "Kalahari Desert"),
    _mc("Which country is famous for the Great Wall?", "China", "Japan", "Mongolia", "Vietnam"),
    _mc("What is the capital city of the Philippines?", "Manila", "Cebu City", "Davao City", "Quezon City"),
    _mc("Mount Everest is located in which mountain range?", "Himalayas", "Andes", "Alps", "Rockies"),
    _mc("Which ocean lies between Africa and Australia?", "Indian Ocean", "Pacific Ocean", "Atlantic Ocean", "Southern Ocean"),
]
_POOL_PH = [
    _mc("Who is the national hero of the Philippines?", "Jose Rizal", "Andres Bonifacio", "Emilio Aguinaldo", "Apolinario Mabini"),
    _mc("What is the capital of Davao del Sur?", "Digos City", "Davao City", "Tagum", "Mati"),
    _mc("What is the Filipino national language?", "Filipino", "English only", "Cebuano only", "Tagalog only"),
    _mc("Which Philippine festival is known as the Festival of Festivals?", "Aliwan Festival", "Sinulog", "Ati-Atihan", "Panagbenga"),
    _mc("What is the currency of the Philippines?", "Philippine Peso", "US Dollar", "Yen", "Euro"),
    _mc("Which island group is Davao located in?", "Mindanao", "Luzon", "Visayas", "Palawan"),
    _mc("What is the national bird of the Philippines?", "Philippine Eagle", "Maya", "Tamaraw", "Carabao"),
    _mc("What is the traditional Filipino clothing for men called?", "Barong Tagalog", "Baro't saya", "Kimono", "Malong"),
    _mc("Who was the first President of the Philippines?", "Emilio Aguinaldo", "Manuel Quezon", "Manuel Roxas", "Sergio Osmeña"),
    _mc("What is the largest island in the Philippines?", "Luzon", "Mindanao", "Palawan", "Negros"),
]
_POOL_ANIME = [
    _mc("Who is Naruto's best friend and rival?", "Sasuke Uchiha", "Shikamaru", "Rock Lee", "Gaara"),
    _mc("What is the name of Luffy's crew in One Piece?", "Straw Hat Pirates", "Heart Pirates", "Kid Pirates", "Red Hair Pirates"),
    _mc("Which anime features Titans?", "Attack on Titan", "Naruto", "Bleach", "One Piece"),
    _mc("Who is the creator of Dragon Ball?", "Akira Toriyama", "Eiichiro Oda", "Masashi Kishimoto", "Hajime Isayama"),
    _mc("What is the name of the notebook in Death Note?", "Death Note", "Life Note", "Shinigami Book", "Shadow Diary"),
    _mc("Which anime character says \"Plus Ultra\"?", "All Might", "Deku", "Endeavor", "Bakugo"),
    _mc("What sport is featured in Haikyuu?", "Volleyball", "Basketball", "Soccer", "Swimming"),
    _mc("Who is Pikachu's trainer?", "Ash Ketchum", "Misty", "Brock", "Gary Oak"),
    _mc("What is Tanjiro's sister's name in Demon Slayer?", "Nezuko", "Zenitsu", "Kanao", "Shinobu"),
    _mc("Which anime features alchemy brothers Edward and Alphonse?", "Fullmetal Alchemist", "Naruto", "Bleach", "Hunter x Hunter"),
]
_POOL_RIDDLES = [
    _mc("What has keys but cannot open locks?", "Piano", "Computer keyboard", "Map", "Treasure chest"),
    _mc("What gets wetter the more it dries?", "Towel", "Sponge", "Soap", "River"),
    _mc("What has hands but cannot clap?", "Clock", "Robot", "Glove", "Watch"),
    _mc("What has a neck but no head?", "Bottle", "Giraffe toy", "Shirt", "Vase"),
    _mc("What can travel around the world while staying in one corner?", "Stamp", "Wind", "Internet signal", "Thought"),
    _mc("What has many teeth but cannot bite?", "Comb", "Zipper", "Saw", "Fork"),
    _mc("What comes once in a minute, twice in a moment, but never in a thousand years?", "The letter M", "The letter N", "Silence", "Air"),
    _mc("What goes up but never comes down?", "Age", "Smoke", "Temperature", "Balloon"),
    _mc("What has one eye but cannot see?", "Needle", "Button", "Storm", "Cyclops"),
    _mc("What begins with T, ends with T, and has tea inside?", "Teapot", "Tent", "Toast", "Treat"),
]

_BUILTIN_QUESTIONS_DATA = {
    'General Knowledge': {'easy': _POOL_GENERAL, 'medium': _POOL_GENERAL, 'hard': _POOL_GENERAL},
    'Science': {'easy': _POOL_SCIENCE, 'medium': _POOL_SCIENCE, 'hard': _POOL_SCIENCE},
    'Technology': {'easy': _POOL_TECH, 'medium': _POOL_TECH, 'hard': _POOL_TECH},
    'Movies & Entertainment': {'easy': _POOL_MOVIES, 'medium': _POOL_MOVIES, 'hard': _POOL_MOVIES},
    'Sports': {'easy': _POOL_SPORTS, 'medium': _POOL_SPORTS, 'hard': _POOL_SPORTS},
    'Mathematics': {'easy': _POOL_MATH, 'medium': _POOL_MATH, 'hard': _POOL_MATH},
    'Geography': {'easy': _POOL_GEO, 'medium': _POOL_GEO, 'hard': _POOL_GEO},
    'Philippine Trivia': {'easy': _POOL_PH, 'medium': _POOL_PH, 'hard': _POOL_PH},
    'Anime': {'easy': _POOL_ANIME, 'medium': _POOL_ANIME, 'hard': _POOL_ANIME},
    'Riddles & Brain Teasers': {'easy': _POOL_RIDDLES, 'medium': _POOL_RIDDLES, 'hard': _POOL_RIDDLES},
}


def question_from_dict(d, difficulty='medium'):
    """Build Question from JSON dict: question, correct, wrong [3] or options [4], explanation optional."""
    qtext = d['question']
    correct = d['correct']
    expl = d.get('explanation') or ''
    if 'options' in d and len(d['options']) >= 4:
        opts = list(d['options'])
        return Question(qtext, opts, correct, difficulty, expl)
    wrong = d.get('wrong') or []
    if len(wrong) < 3:
        wrong = (wrong + ['—', '—', '—'])[:3]
    return Question(qtext, [wrong[0], wrong[1], wrong[2], correct], correct, difficulty, expl)


def load_questions_json(path=QUESTIONS_JSON):
    """Load questions_data structure from JSON file. Returns None if missing/invalid."""
    if not os.path.isfile(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    out = {}
    for cat, tiers in raw.items():
        if not isinstance(tiers, dict):
            continue
        out[cat] = {}
        for diff in ('easy', 'medium', 'hard'):
            lst = tiers.get(diff) or tiers.get('default') or []
            if not lst:
                continue
            out[cat][diff] = [question_from_dict(item, diff) for item in lst]
    return out if out else None


def write_default_questions_json(built_in_data, path=QUESTIONS_JSON):
    """Export built-in bank to JSON for editing (creates/overwrites file)."""
    serial = {}
    for cat, tiers in built_in_data.items():
        serial[cat] = {}
        for diff, plist in tiers.items():
            serial[cat][diff] = [
                {
                    'question': q.question,
                    'correct': q.correct_answer,
                    'wrong': [x for x in q.options if x != q.correct_answer][:3],
                    'explanation': getattr(q, 'explanation', '') or '',
                }
                for q in plist
            ]
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(serial, f, indent=2, ensure_ascii=False)
    except OSError:
        pass


questions_data = load_questions_json() or _BUILTIN_QUESTIONS_DATA
if not os.path.isfile(QUESTIONS_JSON):
    write_default_questions_json(_BUILTIN_QUESTIONS_DATA)


def get_quiz_questions(category, difficulty, session_seed=None):
    """Return QUIZ_QUESTIONS_PER_ROUND questions; order varies by difficulty/session for variety."""
    cat = questions_data.get(category, {})
    pool = cat.get(difficulty) or cat.get('medium') or cat.get('easy') or []
    if not pool:
        return []

    extended = list(pool)
    seed_key = (category, difficulty, session_seed or 0)
    rng = random.Random(hash(seed_key) % (2 ** 32))
    rng.shuffle(extended)

    out = []
    i = 0
    while len(out) < QUIZ_QUESTIONS_PER_ROUND:
        out.append(extended[i % len(extended)])
        i += 1
    return out


# ============ MODERN ROUNDED BUTTON ============
class ModernButton(kivy.uix.button.Button):
    """Rounded multiple-choice option (comic bubble fill + ink outline)."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.background_normal = ''
        self.background_color = (1, 1, 1, 1)
        self.color = (0, 0, 0, 1)
        self.font_size = '18sp'
        self.bold = True
        self.size_hint_y = None
        self.height = kivy.metrics.dp(64)
        self.bind(pos=self._redraw_bubble, size=self._redraw_bubble)
        self.bind(background_color=lambda *_: self._redraw_bubble())

    def _redraw_bubble(self, *args):
        self.canvas.before.clear()
        x, y = self.pos
        w, h = self.size
        r = float(kivy.metrics.dp(16))
        bc = self.background_color
        if len(bc) < 4:
            bc = (1, 1, 1, 1)
        with self.canvas.before:
            Color(bc[0], bc[1], bc[2], bc[3])
            RoundedRectangle(pos=(x, y), size=(w, h), radius=[r])
            Color(0, 0, 0, 1)
            Line(rounded_rectangle=[x, y, w, h, r], width=float(kivy.metrics.dp(2.5)))


class ComicOutlineButton(kivy.uix.button.Button):
    """White (or tinted) comic panel button with thick black outline."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.background_normal = ''
        if 'background_color' not in kwargs:
            self.background_color = (1, 1, 1, 1)
        self.color = (0, 0, 0, 1)
        self.bold = True
        self.bind(pos=self._redraw, size=self._redraw, background_color=lambda *_: self._redraw())

    def _redraw(self, *args):
        self.canvas.before.clear()
        x, y = self.pos
        w, h = self.size
        r = float(kivy.metrics.dp(16))
        bc = self.background_color
        if len(bc) < 4:
            bc = (1, 1, 1, 1)
        with self.canvas.before:
            Color(bc[0], bc[1], bc[2], bc[3])
            RoundedRectangle(pos=(x, y), size=(w, h), radius=[r])
            Color(0, 0, 0, 1)
            Line(rounded_rectangle=[x, y, w, h, r], width=float(kivy.metrics.dp(3)))


class ComicHeroButton(kivy.uix.button.Button):
    """Large ink-filled button with reverse text (primary comic CTA)."""

    def __init__(self, **kwargs):
        fs = kwargs.pop('font_size', '20sp')
        super().__init__(**kwargs)
        self.background_normal = ''
        self.background_color = (0, 0, 0, 0)
        self.color = (1, 1, 1, 1)
        self.bold = True
        self.font_size = fs
        self.bind(pos=self._redraw, size=self._redraw)

    def _redraw(self, *args):
        self.canvas.before.clear()
        x, y = self.pos
        w, h = self.size
        r = float(kivy.metrics.dp(18))
        with self.canvas.before:
            Color(0.06, 0.06, 0.06, 1)
            RoundedRectangle(pos=(x, y), size=(w, h), radius=[r])
            Color(1, 1, 1, 1)
            Line(rounded_rectangle=[x, y, w, h, r], width=float(kivy.metrics.dp(2)))


# ============ QUESTION CARD WIDGET ============
class QuestionCard(kivy.uix.boxlayout.BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.orientation = 'vertical'
        self.padding = [20, 30]
        self.size_hint_y = None
        self.height = kivy.metrics.dp(150)

        with self.canvas.before:
            Color(1, 1, 1, 1)
            RoundedRectangle(pos=self.pos, size=self.size, radius=[20])

    def on_size(self, *args):
        self.canvas.before.clear()
        with self.canvas.before:
            Color(1, 1, 1, 1)
            RoundedRectangle(pos=self.pos, size=self.size, radius=[20])


# ============ THEME MANAGER ============
class ThemeManager:
    light = {
        'bg_primary': (1.0, 0.93, 0.20, 1),
        'bg_card': (1, 1, 1, 1),
        'text_primary': (0.06, 0.06, 0.07, 1),
        'text_secondary': (0.28, 0.28, 0.32, 1),
        'text_accent': (0.06, 0.06, 0.07, 1),
        'button_primary': (0.06, 0.06, 0.07, 1),
        'button_success': (0.16, 0.62, 0.38, 1),
        'button_danger': (0.85, 0.22, 0.22, 1),
        'button_exit': (0.35, 0.35, 0.38, 1),
        'border': (0, 0, 0, 1),
        'score_bg': (1, 1, 1, 1),
        'category_bg': (1, 1, 1, 1),
        'progress': (0.06, 0.06, 0.07, 1),
        'gold': (1.0, 0.84, 0.15, 1),
        'comic_night': False,
    }

    dark = {
        'bg_primary': (0.11, 0.11, 0.13, 1),
        'bg_card': (0.98, 0.98, 1.0, 1),
        'text_primary': (0.98, 0.98, 1.0, 1),
        'text_secondary': (0.82, 0.84, 0.90, 1),
        'text_accent': (1.0, 0.92, 0.35, 1),
        'button_primary': (1.0, 0.93, 0.22, 1),
        'button_success': (0.35, 0.82, 0.55, 1),
        'button_danger': (0.95, 0.45, 0.45, 1),
        'button_exit': (0.55, 0.56, 0.62, 1),
        'border': (1, 1, 1, 1),
        'score_bg': (0.18, 0.18, 0.22, 1),
        'category_bg': (1, 1, 1, 1),
        'progress': (1.0, 0.88, 0.2, 1),
        'gold': (1.0, 0.88, 0.25, 1),
        'comic_night': True,
    }

    light_high_contrast = {
        'bg_primary': (1.0, 1.0, 0.45, 1),
        'bg_card': (1, 1, 1, 1),
        'text_primary': (0, 0, 0, 1),
        'text_secondary': (0.05, 0.05, 0.05, 1),
        'text_accent': (0, 0, 0.5, 1),
        'button_primary': (0, 0, 0, 1),
        'button_success': (0, 0.45, 0.15, 1),
        'button_danger': (0.75, 0, 0, 1),
        'button_exit': (0.2, 0.2, 0.2, 1),
        'border': (0, 0, 0, 1),
        'score_bg': (1, 1, 1, 1),
        'category_bg': (1, 1, 1, 1),
        'progress': (0, 0, 0, 1),
        'gold': (0.85, 0.55, 0, 1),
        'comic_night': False,
    }

    def __init__(self):
        self.current_theme = 'light'
        self.high_contrast = False

    def get_colors(self):
        if self.high_contrast:
            return self.light_high_contrast
        return self.light if self.current_theme == 'light' else self.dark

    def toggle_theme(self):
        self.current_theme = 'dark' if self.current_theme == 'light' else 'light'
        return self.get_colors()


def sync_theme_from_app(theme_manager):
    app = kivy.app.App.get_running_app()
    if app and getattr(app, 'settings', None) is not None and theme_manager:
        theme_manager.high_contrast = bool(app.settings.get('high_contrast'))


# ============ CONFETTI WIDGET ============
class ConfettiWidget(kivy.uix.widget.Widget):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.particles = []
        self.colors = [(1, 0, 0, 1), (0, 1, 0, 1), (0, 0, 1, 1), (1, 1, 0, 1), (1, 0, 1, 1)]
        self.is_running = False
        self.update_event = None

    def start_confetti(self):
        self.is_running = True
        self.particles = []
        width = self.width if self.width > 0 else 400
        height = self.height if self.height > 0 else 700
        for i in range(20):
            x = (i * 13) % int(width)
            y = (i * 7) % int(height)
            color = self.colors[i % len(self.colors)]
            size = 5 + (i % 6)
            self.particles.append({
                'x': x, 'y': y, 'vx': (i % 7) - 3, 'vy': -10 - (i % 6),
                'color': color, 'size': size
            })
        if self.update_event:
            self.update_event.cancel()
        self.update_event = kivy.clock.Clock.schedule_interval(self.update_confetti, 1 / 30)

    def stop_confetti(self):
        self.is_running = False
        self.particles = []
        self.canvas.clear()
        if self.update_event:
            self.update_event.cancel()
            self.update_event = None

    def update_confetti(self, dt):
        if not self.is_running:
            return
        width = self.width if self.width > 0 else 400
        height = self.height if self.height > 0 else 700
        for p in self.particles[:]:
            p['x'] += p['vx']
            p['y'] += p['vy']
            p['vy'] += 0.5
            if p['y'] < 0 or p['x'] < 0 or p['x'] > width:
                self.particles.remove(p)
        self.canvas.clear()
        with self.canvas:
            for p in self.particles:
                kivy.graphics.Color(*p['color'])
                kivy.graphics.Rectangle(pos=(p['x'], p['y']), size=(p['size'], p['size']))


# ============ SCREEN CLASSES ============
class LoginScreen(kivy.uix.screenmanager.Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.theme_manager = None
        self.user_manager = None

    def on_pre_enter(self):
        if self.theme_manager:
            sync_theme_from_app(self.theme_manager)
            colors = self.theme_manager.get_colors()
            self.apply_theme(colors)

    def apply_theme(self, colors):
        if hasattr(self.ids, 'bg_primary'):
            draw_comic_background(self.ids.bg_primary, colors)

    def login(self):
        username = self.ids.username_input.text.strip()
        password = self.ids.password_input.text
        if not username or not password:
            self.ids.message_label.text = "Enter username & password!"
            return
        success, message = self.user_manager.login(username, password)
        self.ids.message_label.text = message
        if success:
            saved_game = self.user_manager.load_quiz_state()
            if saved_game:
                self.show_continue_dialog(saved_game)
            else:
                self.manager.current = 'category'
                profile_screen = self.manager.get_screen('profile')
                profile_screen.load_user_stats()

    def show_continue_dialog(self, saved_game):
        content = kivy.uix.boxlayout.BoxLayout(orientation='vertical', spacing=10, padding=10)
        message = f"You have an unfinished quiz!\n\n"
        message += f"Category: {saved_game['category']}\n"
        message += f"Difficulty: {saved_game['difficulty'].upper()}\n"
        message += f"Progress: Question {saved_game['question_num'] + 1}/{QUIZ_QUESTIONS_PER_ROUND}\n"
        message += f"Score: {saved_game['score']}/{QUIZ_QUESTIONS_PER_ROUND}\n\n"
        message += "Continue where you left off?"

        content.add_widget(kivy.uix.label.Label(
            text=message,
            halign='center',
            color=(0, 0, 0, 1),
            text_size=(kivy.metrics.dp(280), None)
        ))

        buttons = kivy.uix.boxlayout.BoxLayout(size_hint_y=0.3, spacing=10)

        continue_btn = kivy.uix.button.Button(
            text='CONTINUE',
            background_color=(0.2, 0.7, 0.3, 1),
            color=(1, 1, 1, 1)
        )
        continue_btn.bind(on_release=lambda x: self.continue_game(saved_game))

        new_btn = kivy.uix.button.Button(
            text='START NEW',
            background_color=(0.8, 0.3, 0.3, 1),
            color=(1, 1, 1, 1)
        )
        new_btn.bind(on_release=lambda x: self.new_game())

        buttons.add_widget(continue_btn)
        buttons.add_widget(new_btn)
        content.add_widget(buttons)

        popup = kivy.uix.popup.Popup(
            title='Continue Saved Game?',
            content=content,
            size_hint=(0.85, 0.4),
            auto_dismiss=False
        )
        popup.open()
        self.continue_popup = popup

    def continue_game(self, saved_game):
        self.continue_popup.dismiss()
        quiz_screen = self.manager.get_screen('quiz')
        quiz_screen.load_saved_game(saved_game)
        self.manager.current = 'quiz'
        profile_screen = self.manager.get_screen('profile')
        profile_screen.load_user_stats()

    def new_game(self):
        self.continue_popup.dismiss()
        self.user_manager.clear_save()
        self.manager.current = 'category'
        profile_screen = self.manager.get_screen('profile')
        profile_screen.load_user_stats()

    def go_to_register(self):
        self.ids.message_label.text = ""
        self.manager.current = 'register'

    def guest_mode(self):
        self.user_manager.current_user = "guest"
        saved_game = self.user_manager.load_quiz_state()
        if saved_game:
            self.show_continue_dialog(saved_game)
        else:
            self.manager.current = 'category'


class RegisterScreen(kivy.uix.screenmanager.Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.theme_manager = None
        self.user_manager = None

    def on_pre_enter(self):
        if self.theme_manager:
            sync_theme_from_app(self.theme_manager)
            colors = self.theme_manager.get_colors()
            self.apply_theme(colors)

    def apply_theme(self, colors):
        if hasattr(self.ids, 'bg_primary'):
            draw_comic_background(self.ids.bg_primary, colors)

    def register(self):
        username = self.ids.username_input.text.strip()
        password = self.ids.password_input.text
        confirm = self.ids.confirm_input.text
        if not username or not password:
            self.ids.message_label.text = "Username & password required!"
            return
        if password != confirm:
            self.ids.message_label.text = "Passwords don't match!"
            return
        success, message = self.user_manager.register(username, password)
        self.ids.message_label.text = message
        if success:
            self.user_manager.login(username, password)
            self.manager.current = 'category'

    def back_to_login(self):
        self.ids.message_label.text = ""
        self.manager.current = 'login'


class ProfileScreen(kivy.uix.screenmanager.Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.theme_manager = None
        self.user_manager = None

    def on_pre_enter(self):
        if self.theme_manager:
            sync_theme_from_app(self.theme_manager)
            colors = self.theme_manager.get_colors()
            self.apply_theme(colors)
        self.load_user_stats()

    def apply_theme(self, colors):
        if hasattr(self.ids, 'bg_primary'):
            draw_comic_background(self.ids.bg_primary, colors)

    def load_user_stats(self):
        if not self.user_manager or not self.user_manager.is_logged_in():
            self.ids.username_label.text = "Guest Mode"
            self.ids.total_games_value.text = "0"
            self.ids.best_score_value.text = f"0/{QUIZ_QUESTIONS_PER_ROUND}"
            self.ids.avg_score_value.text = f"0.0/{QUIZ_QUESTIONS_PER_ROUND}"
            self.ids.completion_value.text = "0%"
            self.ids.difficulty_box.clear_widgets()
            self.ids.category_box.clear_widgets()
            self.ids.achievements_box.clear_widgets()
            self.ids.recent_box.clear_widgets()
            self.ids.achievements_box.add_widget(kivy.uix.label.Label(
                text="Create an account to unlock and save achievements.",
                size_hint_y=None,
                height=kivy.metrics.dp(36),
                color=(0.45, 0.45, 0.5, 1),
                font_size='11sp'
            ))
            return

        username = self.user_manager.get_current_user()
        self.ids.username_label.text = username
        stats = self.user_manager.get_user_stats()

        if stats:
            total_games = stats.get('total_games', 0)
            total_score = stats.get('total_score', 0)
            best_score = stats.get('best_score', 0)
            avg_score = total_score / total_games if total_games > 0 else 0
            completion = (total_score / (total_games * QUIZ_QUESTIONS_PER_ROUND) * 100) if total_games > 0 else 0

            self.ids.total_games_value.text = str(total_games)
            self.ids.best_score_value.text = f"{best_score}/{QUIZ_QUESTIONS_PER_ROUND}"
            self.ids.avg_score_value.text = f"{avg_score:.1f}/{QUIZ_QUESTIONS_PER_ROUND}"
            self.ids.completion_value.text = f"{completion:.0f}%"

            self.ids.difficulty_box.clear_widgets()
            diff_stats = stats.get('games_by_difficulty', {})
            difficulty_labels = [('easy', 'Easy'), ('medium', 'Medium'), ('hard', 'Hard')]
            for key, title in difficulty_labels:
                data = diff_stats.get(key, {'played': 0, 'best': 0})
                row = kivy.uix.boxlayout.BoxLayout(
                    orientation='horizontal',
                    size_hint_y=None,
                    height=kivy.metrics.dp(30),
                    spacing=6
                )
                row.add_widget(kivy.uix.label.Label(
                    text=title,
                    bold=True,
                    color=(0, 0, 0, 1),
                    font_size='11sp',
                    halign='left'
                ))
                row.add_widget(kivy.uix.label.Label(
                    text=f"{data['played']} games",
                    color=(0.35, 0.35, 0.38, 1),
                    font_size='11sp',
                    halign='center'
                ))
                row.add_widget(kivy.uix.label.Label(
                    text=f"Best {data['best']}/{QUIZ_QUESTIONS_PER_ROUND}",
                    color=(0, 0, 0, 1),
                    font_size='11sp',
                    halign='right',
                    bold=True
                ))
                self.ids.difficulty_box.add_widget(row)

            self.ids.category_box.clear_widgets()
            categories = stats.get('games_by_category', {})
            if categories:
                ordered_categories = sorted(categories.items(), key=lambda x: x[1].get('played', 0), reverse=True)
                for cat, data in ordered_categories:
                    row = kivy.uix.gridlayout.GridLayout(
                        cols=3,
                        size_hint_y=None,
                        height=kivy.metrics.dp(28),
                        spacing=5
                    )
                    row.add_widget(kivy.uix.label.Label(
                        text=cat,
                        color=(0, 0, 0, 1),
                        font_size='11sp',
                        bold=True
                    ))
                    row.add_widget(kivy.uix.label.Label(
                        text=f"{data['played']} played",
                        color=(0.35, 0.35, 0.38, 1),
                        font_size='10sp'
                    ))
                    row.add_widget(kivy.uix.label.Label(
                        text=f"Best {data['best']}/{QUIZ_QUESTIONS_PER_ROUND}",
                        color=(0, 0, 0, 1),
                        font_size='10sp',
                        bold=True
                    ))
                    self.ids.category_box.add_widget(row)
            else:
                self.ids.category_box.add_widget(kivy.uix.label.Label(
                    text="Play different categories to see your strengths here.",
                    size_hint_y=None,
                    height=kivy.metrics.dp(32),
                    color=(0.45, 0.45, 0.5, 1),
                    font_size='10sp'
                ))

            self.ids.achievements_box.clear_widgets()
            achievements = stats.get('achievements', [])
            achievement_info = {
                'PERFECT_SCORE': ('🏆 Perfect Score', 'Clear a full quiz with no mistakes.'),
                'EXCELLENT': ('⭐ Excellent', 'Finish a round scoring at least 80%.'),
                'EASY_MASTER': ('🌟 Easy Master', 'Perfect score on Easy difficulty.'),
                'MEDIUM_MASTER': ('🌟 Medium Master', 'Perfect score on Medium difficulty.'),
                'HARD_MASTER': ('🌟 Hard Master', 'Perfect score on Hard difficulty.'),
            }

            if achievements:
                for ach in achievements:
                    title, desc = achievement_info.get(ach, (ach, ''))
                    body = f"{title}\n{desc}" if desc else title
                    ach_label = kivy.uix.label.Label(
                        text=body,
                        size_hint_y=None,
                        height=kivy.metrics.dp(52),
                        color=(0, 0, 0, 1),
                        font_size='10sp',
                        halign='left',
                        valign='top',
                        text_size=(kivy.metrics.dp(330), None),
                    )
                    self.ids.achievements_box.add_widget(ach_label)
            else:
                no_ach = kivy.uix.label.Label(
                    text="No achievements yet. Keep playing!",
                    size_hint_y=None,
                    height=kivy.metrics.dp(28),
                    color=(0.5, 0.5, 0.5, 1),
                    font_size='10sp'
                )
                self.ids.achievements_box.add_widget(no_ach)

            recent_games = stats.get('recent_games', [])
            self.ids.recent_box.clear_widgets()

            if recent_games:
                header = kivy.uix.gridlayout.GridLayout(cols=4, size_hint_y=None, height=kivy.metrics.dp(30), spacing=5)
                header.add_widget(
                    kivy.uix.label.Label(text='CATEGORY', bold=True, color=(0, 0, 0, 1), font_size='11sp'))
                header.add_widget(
                    kivy.uix.label.Label(text='LEVEL', bold=True, color=(0, 0, 0, 1), font_size='11sp'))
                header.add_widget(
                    kivy.uix.label.Label(text='SCORE', bold=True, color=(0, 0, 0, 1), font_size='11sp'))
                header.add_widget(
                    kivy.uix.label.Label(text='TIME', bold=True, color=(0, 0, 0, 1), font_size='11sp'))
                self.ids.recent_box.add_widget(header)

                for game in recent_games[:10]:
                    game_row = kivy.uix.gridlayout.GridLayout(cols=4, size_hint_y=None, height=kivy.metrics.dp(25),
                                                              spacing=5)
                    cat_color = (0, 0, 0, 1) if game['score'] == game['total'] else (0.28, 0.28, 0.3, 1)
                    game_row.add_widget(kivy.uix.label.Label(text=game['category'], color=cat_color, font_size='10sp'))

                    diff_color = (0, 0, 0, 1)
                    game_row.add_widget(
                        kivy.uix.label.Label(text=game['difficulty'].upper(), color=diff_color, font_size='10sp',
                                             bold=True))

                    score_color = (0, 0, 0, 1) if game['score'] == game['total'] else (0.45, 0.45, 0.48, 1)
                    game_row.add_widget(kivy.uix.label.Label(text=f"{game['score']}/{game['total']}", color=score_color,
                                                             font_size='10sp', bold=True))
                    game_row.add_widget(
                        kivy.uix.label.Label(text=f"{game['time']}s", color=(0.5, 0.5, 0.5, 1), font_size='10sp'))

                    self.ids.recent_box.add_widget(game_row)
            else:
                no_games = kivy.uix.label.Label(
                    text="No games played yet. Start a quiz!",
                    size_hint_y=None,
                    height=kivy.metrics.dp(40),
                    color=(0.5, 0.5, 0.5, 1),
                    font_size='12sp'
                )
                self.ids.recent_box.add_widget(no_games)

    def logout(self):
        self.user_manager.logout()
        self.manager.current = 'login'

    def back_to_game(self):
        self.manager.current = 'category'


class CategoryScreen(kivy.uix.screenmanager.Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.theme_manager = None
        self.user_manager = None

    def on_pre_enter(self):
        if self.theme_manager:
            sync_theme_from_app(self.theme_manager)
            colors = self.theme_manager.get_colors()
            self.apply_theme(colors)
        if hasattr(self.ids, 'daily_spotlight_label'):
            self.ids.daily_spotlight_label.text = (
                f"Daily spotlight: {daily_spotlight_category()} — bonus bragging rights!"
            )

    def apply_theme(self, colors):
        if hasattr(self.ids, 'bg_primary'):
            draw_comic_background(self.ids.bg_primary, colors)

    def show_leaderboard(self):
        from kivy.uix.popup import Popup
        from kivy.uix.scrollview import ScrollView
        from kivy.uix.label import Label
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.button import Button
        from kivy.metrics import dp

        app = kivy.app.App.get_running_app()
        rows = []
        if getattr(app, 'leaderboard', None):
            rows = app.leaderboard.load()[:25]

        def _solid_white_bg(widget):
            """White fill without clearing canvas.before (clear() breaks ScrollView and some widgets)."""
            with widget.canvas.before:
                Color(1, 1, 1, 1)
                rect = Rectangle(pos=widget.pos, size=widget.size)

            def _upd(*_a):
                rect.pos = widget.pos
                rect.size = widget.size

            widget.bind(pos=_upd, size=_upd)

        content = BoxLayout(orientation='vertical', padding=dp(10), spacing=dp(8))
        _solid_white_bg(content)

        scroll = ScrollView(size_hint_y=1)
        inner = BoxLayout(orientation='vertical', size_hint_y=None, spacing=dp(4))
        inner.bind(minimum_height=inner.setter('height'))
        _solid_white_bg(inner)

        txt_color = (0.08, 0.09, 0.12, 1)
        sub_color = (0.28, 0.30, 0.36, 1)

        if not rows:
            inner.add_widget(Label(
                text='No scores yet.\nFinish a quiz and your best runs will show here!',
                size_hint_y=None,
                height=dp(96),
                color=sub_color,
                font_size='13sp',
                halign='center',
                valign='middle',
                text_size=(dp(320), None),
            ))
        else:
            inner.add_widget(Label(
                text='TOP RUNS (best score, streak)',
                size_hint_y=None,
                height=dp(28),
                font_size='12sp',
                bold=True,
                color=txt_color,
                halign='left',
                text_size=(dp(340), None),
            ))
            for i, r in enumerate(rows, 1):
                line = (
                    f"{i}. {r.get('name', '?')}  ·  {r.get('score', 0)}/{r.get('total', 10)}  ·  "
                    f"{r.get('category', '')[:14]}  ·  streak {r.get('streak', 0)}"
                )
                inner.add_widget(Label(
                    text=line,
                    size_hint_y=None,
                    height=dp(34),
                    text_size=(dp(340), None),
                    halign='left',
                    valign='middle',
                    font_size='12sp',
                    color=txt_color,
                ))
        scroll.add_widget(inner)
        content.add_widget(scroll)

        close_btn = Button(
            text='CLOSE', size_hint_y=None, height=dp(48), bold=True,
            background_color=(0.15, 0.45, 0.95, 1),
            color=(1, 1, 1, 1),
        )
        content.add_widget(close_btn)

        popup = Popup(
            title='Leaderboard',
            title_size='20sp',
            title_color=(0.08, 0.09, 0.12, 1),
            title_align='center',
            separator_color=(0.75, 0.78, 0.88, 1),
            content=content,
            size_hint=(0.92, 0.72),
            auto_dismiss=True,
        )
        popup.background_color = (1, 1, 1, 1)
        popup.separator_height = dp(2)
        close_btn.bind(on_release=popup.dismiss)
        popup.open()

    def show_settings(self):
        from kivy.uix.popup import Popup
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.label import Label
        from kivy.uix.button import Button
        from kivy.uix.switch import Switch
        from kivy.metrics import dp

        app = kivy.app.App.get_running_app()
        content = BoxLayout(orientation='vertical', padding=dp(14), spacing=dp(12))

        row1 = BoxLayout(size_hint_y=None, height=dp(46))
        row1.add_widget(Label(text='Sound effects', size_hint_x=0.62, halign='left'))
        sw_sound = Switch(active=bool(app.settings.get('sound_enabled', True)))
        row1.add_widget(sw_sound)
        content.add_widget(row1)

        def on_sound(_inst, val):
            app.settings['sound_enabled'] = val
            save_app_settings(app.settings)

        sw_sound.bind(active=on_sound)

        row2 = BoxLayout(size_hint_y=None, height=dp(46))
        row2.add_widget(Label(text='High contrast', size_hint_x=0.62, halign='left'))
        sw_hc = Switch(active=bool(app.settings.get('high_contrast', False)))
        row2.add_widget(sw_hc)
        content.add_widget(row2)

        def on_hc(_inst, val):
            app.settings['high_contrast'] = val
            save_app_settings(app.settings)
            if self.theme_manager:
                sync_theme_from_app(self.theme_manager)
                self.apply_theme(self.theme_manager.get_colors())

        sw_hc.bind(active=on_hc)

        close_btn = Button(text='DONE', size_hint_y=None, height=dp(50), bold=True)
        content.add_widget(close_btn)

        popup = Popup(title='Settings', content=content, size_hint=(0.88, 0.42), auto_dismiss=True)
        close_btn.bind(on_release=popup.dismiss)
        popup.open()

    def show_instructions(self):
        from kivy.uix.popup import Popup
        from kivy.uix.scrollview import ScrollView
        from kivy.uix.label import Label
        from kivy.uix.button import Button
        from kivy.uix.boxlayout import BoxLayout
        from kivy.metrics import dp

        instructions = """
[b]STEP 1 — PICK A TOPIC[/b]
Scroll the list and tap one of 10 categories (General Knowledge, Science, Technology, and more).

[b]STEP 2 — CHOOSE DIFFICULTY[/b]
Easy (45s) • Medium (30s) • Hard (15s)

[b]STEP 3 — ANSWER[/b]
Read the speech bubble question and tap a rounded answer.

[b]STEP 4 — WIN STUFF[/b]
Score perfect runs to unlock achievements.

[b]GOOD LUCK![/b]
""".strip()

        content = BoxLayout(orientation='vertical', spacing=dp(12), padding=dp(18), size_hint=(1, 1))

        with content.canvas.before:
            Color(1, 1, 1, 1)
            content._instr_panel = Rectangle(pos=content.pos, size=content.size)

        def _instr_panel_bg(*_args):
            content._instr_panel.pos = content.pos
            content._instr_panel.size = content.size

        content.bind(pos=_instr_panel_bg, size=_instr_panel_bg)

        title = Label(
            text='HOW TO PLAY',
            font_size='24sp',
            bold=True,
            color=(0, 0, 0, 1),
            size_hint_y=None,
            height=dp(44),
            halign='left',
            valign='middle',
        )
        content.add_widget(title)

        scroll = ScrollView(
            size_hint_y=1,
            do_scroll_x=False,
            bar_width=dp(10),
            scroll_type=['bars', 'content'],
        )

        text_label = Label(
            text=instructions,
            markup=True,
            font_size='15sp',
            line_height=1.35,
            halign='left',
            valign='top',
            color=(0.06, 0.06, 0.08, 1),
            size_hint_y=None,
            text_size=(dp(280), None),
        )

        def _instruction_heights(*_args):
            tw = scroll.width - dp(28)
            if tw <= dp(120):
                tw = max(kivy.core.window.Window.width * 0.85 - dp(56), dp(220))
            text_label.text_size = (tw, None)
            text_label.texture_update()
            text_label.height = max(text_label.texture_size[1] + dp(8), dp(120))

        def _title_fit(*_args):
            tw = max(content.width - dp(36), dp(160))
            title.text_size = (tw, None)

        scroll.bind(width=_instruction_heights)
        content.bind(width=_title_fit)

        scroll.add_widget(text_label)
        content.add_widget(scroll)

        close_btn = Button(
            text='GOT IT!',
            size_hint_y=None,
            height=dp(54),
            background_color=(0.06, 0.06, 0.06, 1),
            color=(1, 1, 1, 1),
            font_size='17sp',
            bold=True,
        )
        content.add_widget(close_btn)

        popup = Popup(
            title='',
            separator_height=0,
            content=content,
            size_hint=(0.92, 0.82),
            auto_dismiss=True,
        )
        popup.background = ''
        popup.background_color = (1.0, 0.97, 0.88, 1)
        popup.border = (0, 0, 0, 0)

        close_btn.bind(on_release=popup.dismiss)

        def _after_open(*_a):
            _title_fit()
            _instruction_heights()

        popup.bind(
            on_open=lambda *_x: kivy.clock.Clock.schedule_once(_after_open, 0)
        )

        popup.open()
        kivy.clock.Clock.schedule_once(_after_open, 0.12)


class DifficultyScreen(kivy.uix.screenmanager.Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.theme_manager = None
        self.selected_category = None

    def on_pre_enter(self):
        if self.theme_manager:
            sync_theme_from_app(self.theme_manager)
            colors = self.theme_manager.get_colors()
            self.apply_theme(colors)

    def apply_theme(self, colors):
        if hasattr(self.ids, 'bg_primary'):
            draw_comic_background(self.ids.bg_primary, colors)

    def set_category(self, category):
        self.selected_category = category
        self.ids.category_label.text = category.upper()

    def start_quiz(self, difficulty):
        if self.selected_category:
            quiz_screen = self.manager.get_screen('quiz')
            quiz_screen.start_quiz(self.selected_category, difficulty)
            self.manager.current = 'quiz'


class QuizScreen(kivy.uix.screenmanager.Screen):
    question_num = kivy.properties.NumericProperty(0)
    score = kivy.properties.NumericProperty(0)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.questions = []
        self.current_question = None
        self.answered = False
        self.time_left = 30
        self.time_limit = 30
        self.timer_running = False
        self.start_time = 0
        self.timer_event = None
        self.exit_popup = None
        self.theme_manager = None
        self.user_manager = None
        self.category = ""
        self.difficulty = ""
        self.is_saved_game = False
        self._quiz_seed = 0
        self.streak_count = 0
        self.max_streak = 0
        self.paused = False

    def on_pre_enter(self):
        if self.theme_manager:
            sync_theme_from_app(self.theme_manager)
            colors = self.theme_manager.get_colors()
            self.apply_theme(colors)

    def apply_theme(self, colors):
        if hasattr(self.ids, 'bg_primary'):
            draw_comic_background(self.ids.bg_primary, colors)
        if hasattr(self.ids, 'question_label'):
            self.ids.question_label.color = colors['text_primary']

    def load_saved_game(self, saved_state):
        self.is_saved_game = True
        self.category = saved_state['category']
        self.difficulty = saved_state['difficulty']

        if self.difficulty == 'easy':
            self.time_limit = 45
        elif self.difficulty == 'medium':
            self.time_limit = 30
        else:
            self.time_limit = 15

        self._quiz_seed = saved_state.get('seed', random.randint(1, 2 ** 30))
        self.questions = get_quiz_questions(self.category, self.difficulty, self._quiz_seed)

        self.question_num = saved_state['question_num']
        self.score = saved_state['score']
        self.answered = False
        self.start_time = kivy.clock.Clock.get_time()
        self.streak_count = saved_state.get('streak', 0)
        self.max_streak = saved_state.get('max_streak', 0)
        self.paused = False
        if hasattr(self.ids, 'streak_label'):
            self.ids.streak_label.text = f"STREAK x{self.streak_count}"

        self.ids.progress_bar.max = len(self.questions)
        self.ids.progress_bar.value = self.question_num
        self.ids.category_label.text = f"{self.category.upper()} - {self.difficulty.upper()}"
        self.display_question()

    def start_quiz(self, category, difficulty='medium'):
        self.is_saved_game = False
        self.category = category
        self.difficulty = difficulty

        if difficulty == 'easy':
            self.time_limit = 45
        elif difficulty == 'medium':
            self.time_limit = 30
        else:
            self.time_limit = 15

        self._quiz_seed = random.randint(1, 2 ** 30)
        self.questions = get_quiz_questions(category, difficulty, self._quiz_seed)

        self.question_num = 0
        self.score = 0
        self.answered = False
        self.start_time = kivy.clock.Clock.get_time()
        self.streak_count = 0
        self.max_streak = 0
        self.paused = False
        if hasattr(self.ids, 'streak_label'):
            self.ids.streak_label.text = "STREAK x0"
        self.ids.progress_bar.max = len(self.questions)
        self.ids.progress_bar.value = 0
        self.ids.category_label.text = f"{category.upper()} - {difficulty.upper()}"
        self.display_question()

    def toggle_pause(self):
        if self.answered:
            return
        self.paused = not self.paused
        if hasattr(self.ids, 'pause_btn'):
            self.ids.pause_btn.text = 'GO' if self.paused else '||'
        if self.paused:
            self.timer_running = False
            if self.timer_event:
                self.timer_event.cancel()
                self.timer_event = None
        else:
            self.timer_running = True
            if self.timer_event:
                self.timer_event.cancel()
            self.timer_event = kivy.clock.Clock.schedule_interval(self.update_timer, 1)

    def display_question(self):
        if self.question_num < len(self.questions):
            self.current_question = self.questions[self.question_num]
            self.time_left = self.time_limit
            self.timer_running = not self.paused
            self.ids.timer_bar.value = 100
            self.ids.timer_label.text = f"TIME: {self.time_left}s"
            self.ids.timer_label.color = (0, 0, 0, 1)
            self.ids.progress_bar.value = self.question_num
            self.ids.feedback_label.text = ""

            self.ids.question_label.text = self.current_question.question
            self.ids.counter_label.text = f"Q{self.question_num + 1}/{len(self.questions)}"
            self.ids.score_label.text = f"SCORE: {self.score}"

            self.ids.answers_box.clear_widgets()

            opts = list(self.current_question.options)
            random.shuffle(opts)
            for option in opts:
                btn = ModernButton(text=option)
                btn.bind(on_release=self.check_answer)
                self.ids.answers_box.add_widget(btn)

            if self.timer_event:
                self.timer_event.cancel()
            if not self.paused:
                self.timer_event = kivy.clock.Clock.schedule_interval(self.update_timer, 1)

    def update_timer(self, dt):
        if self.paused:
            return True
        if self.timer_running and self.time_left > 0 and not self.answered:
            self.time_left -= 1
            self.ids.timer_bar.value = (self.time_left / self.time_limit) * 100
            self.ids.timer_label.text = f"TIME: {self.time_left}s"

            if self.time_left <= 5:
                self.ids.timer_label.color = (1, 0, 0, 1)
            return True
        elif self.time_left <= 0 and not self.answered:
            self.timer_running = False
            self.answered = True
            self.ids.timer_label.text = "TIME'S UP!"
            self.ids.feedback_label.text = "Time's up!"
            self.ids.feedback_label.color = (0.9, 0.3, 0.3, 1)
            if self.timer_event:
                self.timer_event.cancel()
            self.streak_count = 0
            if hasattr(self.ids, 'streak_label'):
                self.ids.streak_label.text = "STREAK x0"

            if self.question_num == len(self.questions) - 1:
                self.finish_quiz()
            else:
                kivy.clock.Clock.schedule_once(self.auto_wrong, 1)
            return False
        return False

    def auto_wrong(self, dt):
        for child in self.ids.answers_box.children:
            child.disabled = True
            if child.text == self.current_question.correct_answer:
                child.background_color = (0, 1, 0, 1)

        if self.question_num < len(self.questions) - 1:
            kivy.clock.Clock.schedule_once(self.next_question, 2.0)
        else:
            kivy.clock.Clock.schedule_once(self.finish_quiz, 2.0)

    def check_answer(self, instance):
        if self.answered:
            return
        self.answered = True
        self.timer_running = False
        if self.timer_event:
            self.timer_event.cancel()

        try:
            kivy.app.App.get_running_app().play_feedback('correct' if instance.text == self.current_question.correct_answer else 'wrong')
        except Exception:
            pass

        if instance.text == self.current_question.correct_answer:
            instance.background_color = (0.2, 0.7, 0.4, 1)
            self.score += 1
            self.streak_count += 1
            self.max_streak = max(self.max_streak, self.streak_count)
            if hasattr(self.ids, 'streak_label'):
                self.ids.streak_label.text = f"STREAK x{self.streak_count}"
            self.ids.score_label.text = f"SCORE: {self.score}"
            tip = (self.current_question.explanation or '').strip()
            if not tip:
                tip = f"The answer is {self.current_question.correct_answer}."
            self.ids.feedback_label.text = f"Correct!\nTip: {tip}"
            self.ids.feedback_label.color = (0.2, 0.7, 0.4, 1)
        else:
            instance.background_color = (0.9, 0.3, 0.3, 1)
            self.streak_count = 0
            if hasattr(self.ids, 'streak_label'):
                self.ids.streak_label.text = "STREAK x0"
            tip = (self.current_question.explanation or '').strip()
            if not tip:
                tip = f"The correct answer was {self.current_question.correct_answer}."
            self.ids.feedback_label.text = f"Wrong!\nTip: {tip}"
            self.ids.feedback_label.color = (0.9, 0.3, 0.3, 1)
            for child in self.ids.answers_box.children:
                if child.text == self.current_question.correct_answer:
                    child.background_color = (0.2, 0.7, 0.4, 1)

        for child in self.ids.answers_box.children:
            child.disabled = True

        delay = 2.0 if (self.current_question.explanation or '').strip() else 1.2
        if self.question_num < len(self.questions) - 1:
            kivy.clock.Clock.schedule_once(self.next_question, delay)
        else:
            kivy.clock.Clock.schedule_once(self.finish_quiz, delay)

    def next_question(self, dt):
        self.question_num += 1
        self.answered = False
        self.display_question()

    def finish_quiz(self, dt=None):
        end_time = kivy.clock.Clock.get_time()
        time_taken = int(end_time - self.start_time)

        if self.user_manager and self.user_manager.is_logged_in():
            self.user_manager.update_stats(self.score, len(self.questions), self.category, self.difficulty, time_taken)

        self.user_manager.clear_save()

        result_screen = self.manager.get_screen('result')
        result_screen.display_results(self.score, self.category, self.difficulty, time_taken, len(self.questions),
                                     streak=self.max_streak)

        try:
            app = kivy.app.App.get_running_app()
            if getattr(app, 'leaderboard', None):
                name = 'Guest'
                if self.user_manager and self.user_manager.get_current_user():
                    name = self.user_manager.get_current_user()
                app.leaderboard.add_entry(name, self.score, len(self.questions), self.category,
                                          self.difficulty, self.max_streak)
        except Exception:
            pass

        if self.score == len(self.questions):
            kivy.clock.Clock.schedule_once(lambda dt: result_screen.start_confetti(), 0.5)

        self.manager.current = 'result'

    def exit_to_category(self):
        self.show_exit_confirmation()

    def show_exit_confirmation(self):
        if self.timer_event:
            self.timer_event.cancel()

        content = kivy.uix.boxlayout.BoxLayout(orientation='vertical', spacing=10, padding=10)

        if not self.is_saved_game and self.question_num > 0:
            message = "Leave quiz now?\nYour progress will be saved."
        else:
            message = "Leave quiz now?\nCurrent progress will be lost."

        content.add_widget(kivy.uix.label.Label(text=message, halign='center', color=(0, 0, 0, 1)))

        buttons = kivy.uix.boxlayout.BoxLayout(size_hint_y=0.4, spacing=10)

        yes_btn = kivy.uix.button.Button(text='LEAVE', background_color=(0.8, 0.3, 0.3, 1), color=(1, 1, 1, 1))
        yes_btn.bind(on_release=lambda x: self.confirm_exit())

        no_btn = kivy.uix.button.Button(text='STAY', background_color=(0.3, 0.65, 0.95, 1), color=(1, 1, 1, 1))
        no_btn.bind(on_release=self.dismiss_popup)

        buttons.add_widget(yes_btn)
        buttons.add_widget(no_btn)
        content.add_widget(buttons)

        self.exit_popup = kivy.uix.popup.Popup(title='Exit Quiz', content=content, size_hint=(0.7, 0.3),
                                               auto_dismiss=False)
        self.exit_popup.open()

    def dismiss_popup(self, instance):
        if hasattr(self, 'exit_popup') and self.exit_popup:
            self.exit_popup.dismiss()
            if not self.answered and not self.paused and self.timer_running:
                if self.timer_event:
                    self.timer_event.cancel()
                self.timer_event = kivy.clock.Clock.schedule_interval(self.update_timer, 1)

    def confirm_exit(self):
        self.dismiss_popup(None)

        if not self.is_saved_game and self.question_num > 0:
            self.user_manager.save_quiz_state(
                self.category, self.difficulty, self.question_num,
                self.score, self.time_left, self.question_num + 1,
                seed=getattr(self, '_quiz_seed', 0),
                streak=self.streak_count,
                max_streak=self.max_streak,
            )

        self.reset_quiz()
        self.manager.current = 'category'

    def reset_quiz(self):
        self.question_num = 0
        self.score = 0
        self.answered = False
        self.timer_running = False
        self.paused = False
        if hasattr(self.ids, 'pause_btn'):
            self.ids.pause_btn.text = '||'
        if self.timer_event:
            self.timer_event.cancel()
            self.timer_event = None
        self.ids.answers_box.clear_widgets()


class ResultScreen(kivy.uix.screenmanager.Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.confetti = None
        self.category = ""
        self.difficulty = ""
        self.theme_manager = None

    def on_enter(self):
        if not self.confetti:
            self.confetti = ConfettiWidget()
            self.add_widget(self.confetti)

    def on_pre_enter(self):
        if self.theme_manager:
            sync_theme_from_app(self.theme_manager)
            colors = self.theme_manager.get_colors()
            self.apply_theme(colors)

    def on_leave(self):
        if self.confetti:
            self.confetti.stop_confetti()

    def apply_theme(self, colors):
        if hasattr(self.ids, 'bg_primary'):
            draw_comic_background(self.ids.bg_primary, colors)

    def display_results(self, score, category, difficulty, time_taken, total, streak=0):
        self.ids.score_label.text = f"{score}/{total}"
        self.category = category
        self.difficulty = difficulty

        if hasattr(self.ids, 'streak_result_label'):
            self.ids.streak_result_label.text = f"BEST STREAK: {streak}" if streak else ''

        percentage = (score / total) * 100
        if percentage == 100:
            message = f"KAPOW! {difficulty.upper()} MASTER!"
            self.ids.message_label.color = (0, 0, 0, 1)
        elif percentage >= 80:
            message = "WOW! THAT WAS AMAZING!"
            self.ids.message_label.color = (0, 0, 0, 1)
        elif percentage >= 60:
            message = "NICE! KEEP CLIMBING!"
            self.ids.message_label.color = (0, 0, 0, 1)
        else:
            message = "TRAIN MORE — YOU GOT THIS!"
            self.ids.message_label.color = (0.45, 0.08, 0.08, 1)

        self.ids.message_label.text = message
        self.ids.time_label.text = f"TIME: {time_taken}s  |  {difficulty.upper()}"

        # Show win message like in the photo
        if score == total:
            self.ids.win_label.text = "YOU WIN!\n★ PERFECT RUN ★"
            self.ids.win_label.opacity = 1
        else:
            self.ids.win_label.text = ''
            self.ids.win_label.opacity = 0

    def start_confetti(self):
        if self.confetti:
            self.confetti.start_confetti()

    def play_again(self):
        if self.confetti:
            self.confetti.stop_confetti()
        self.manager.current = 'category'

    def retry_category(self):
        if self.confetti:
            self.confetti.stop_confetti()
        quiz_screen = self.manager.get_screen('quiz')
        quiz_screen.start_quiz(self.category, self.difficulty)
        self.manager.current = 'quiz'


# ============ MAIN APP ============
class QuizApp(kivy.app.App):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.theme_manager = ThemeManager()
        self.user_manager = UserManager()
        self.settings = load_app_settings()
        self.leaderboard = LeaderboardStore()
        ensure_feedback_sound_files()

    def play_feedback(self, kind):
        """kind: 'correct' | 'wrong' — plays sound if enabled and asset exists."""
        if not self.settings.get('sound_enabled', True):
            return
        try:
            from kivy.core.audio import SoundLoader
            base = os.path.join(_APP_DIR, 'assets', 'sounds')
            fname = 'correct.wav' if kind == 'correct' else 'wrong.wav'
            path = os.path.join(base, fname)
            if os.path.isfile(path):
                s = SoundLoader.load(path)
                if s:
                    s.play()
        except Exception:
            pass

    def build(self):
        self.title = "Quiz Master"

        kv_string = '''
<PrimaryButton@ComicHeroButton>:
    font_size: '18sp'
    size_hint_y: None
    height: dp(58)

<SuccessButton@ComicOutlineButton>:
    background_color: (1, 1, 1, 1)
    color: (0, 0, 0, 1)
    font_size: '16sp'
    size_hint_y: None
    height: dp(54)

<DangerButton@ComicOutlineButton>:
    background_color: (1, 0.9, 0.9, 1)
    color: (0.45, 0, 0, 1)
    font_size: '16sp'
    size_hint_y: None
    height: dp(54)

<NeutralButton@ComicOutlineButton>:
    background_color: (0.93, 0.93, 0.95, 1)
    color: (0.06, 0.06, 0.07, 1)
    font_size: '14sp'
    size_hint_y: None
    height: dp(50)

<StyledInput@TextInput>:
    multiline: False
    font_size: '15sp'
    size_hint_y: None
    height: dp(50)
    padding: [14, 14, 14, 14]
    background_color: (1, 1, 1, 1)
    foreground_color: (0.06, 0.06, 0.07, 1)
    cursor_color: (0, 0, 0, 1)
    hint_text_color: (0.45, 0.45, 0.48, 1)

<LoginScreen>:
    BoxLayout:
        id: bg_primary
        orientation: 'vertical'
        padding: 22
        spacing: 12

        Label:
            text: 'QUIZ MASTER!'
            size_hint_y: 0.11
            font_size: '40sp'
            bold: True
            color: (0, 0, 0, 1)

        BoxLayout:
            orientation: 'vertical'
            size_hint_y: 0.14
            padding: 14
            spacing: 6
            canvas.before:
                Color:
                    rgba: 1, 1, 1, 1
                RoundedRectangle:
                    pos: self.pos
                    size: self.size
                    radius: [18]
                Color:
                    rgba: 0, 0, 0, 1
                Line:
                    width: 3
                    rounded_rectangle: (self.x, self.y, self.width, self.height, 18)

            Label:
                text: 'Welcome back, hero!'
                font_size: '17sp'
                bold: True
                color: (0, 0, 0, 1)
                size_hint_y: None
                height: dp(26)

            Label:
                text: 'Challenge your mind. Conquer the quiz.'
                font_size: '12sp'
                italic: True
                color: (0.12, 0.12, 0.14, 1)
                halign: 'center'
                valign: 'middle'
                text_size: self.width - 8, None
                size_hint_y: None
                height: dp(44)

        BoxLayout:
            orientation: 'vertical'
            size_hint_y: 0.38
            spacing: 12
            padding: [4, 6]

            StyledInput:
                id: username_input
                hint_text: 'Username'
                size_hint_y: None

            StyledInput:
                id: password_input
                hint_text: 'Password'
                password: True
                size_hint_y: None

            PrimaryButton:
                text: "LOGIN — LET'S GO!"
                font_size: '19sp'
                height: dp(60)
                on_release: root.login()

        BoxLayout:
            orientation: 'vertical'
            size_hint_y: 0.22
            spacing: 10
            padding: [4, 2]

            SuccessButton:
                text: 'NEW HERO — SIGN UP'
                height: dp(52)
                on_release: root.go_to_register()

            NeutralButton:
                text: 'GUEST MODE'
                height: dp(48)
                on_release: root.guest_mode()

        Label:
            id: message_label
            text: ''
            size_hint_y: 0.07
            font_size: '12sp'
            bold: True
            color: (0.75, 0.05, 0.05, 1)

<RegisterScreen>:
    BoxLayout:
        id: bg_primary
        orientation: 'vertical'
        padding: 22
        spacing: 10

        Label:
            text: 'JOIN THE FUN!'
            size_hint_y: 0.1
            font_size: '30sp'
            bold: True
            color: (0, 0, 0, 1)

        BoxLayout:
            orientation: 'vertical'
            size_hint_y: 0.1
            padding: 12
            spacing: 4
            canvas.before:
                Color:
                    rgba: 1, 1, 1, 1
                RoundedRectangle:
                    pos: self.pos
                    size: self.size
                    radius: [16]
                Color:
                    rgba: 0, 0, 0, 1
                Line:
                    width: 3
                    rounded_rectangle: (self.x, self.y, self.width, self.height, 16)

            Label:
                text: 'Create your comic-book hero profile.'
                font_size: '13sp'
                italic: True
                color: (0.12, 0.12, 0.14, 1)
                halign: 'center'
                text_size: self.width - 8, None

        BoxLayout:
            orientation: 'vertical'
            size_hint_y: 0.55
            spacing: 8
            padding: [4, 4]

            StyledInput:
                id: username_input
                hint_text: 'Username (3+ chars)'

            StyledInput:
                id: email_input
                hint_text: 'Email (optional)'

            StyledInput:
                id: password_input
                hint_text: 'Password (4+ chars)'
                password: True

            StyledInput:
                id: confirm_input
                hint_text: 'Confirm Password'
                password: True

            PrimaryButton:
                text: 'REGISTER — BOOM!'
                font_size: '18sp'
                height: dp(58)
                on_release: root.register()

        NeutralButton:
            text: 'BACK'
            size_hint_y: 0.09
            on_release: root.back_to_login()

        Label:
            id: message_label
            text: ''
            size_hint_y: 0.07
            font_size: '12sp'
            bold: True
            color: (0.75, 0.05, 0.05, 1)

<CategoryScreen>:
    BoxLayout:
        id: bg_primary
        orientation: 'vertical'
        padding: 16
        spacing: 10

        Label:
            text: 'QUIZ MASTER!'
            size_hint_y: None
            height: dp(48)
            font_size: '34sp'
            bold: True
            color: (0, 0, 0, 1)

        Label:
            id: daily_spotlight_label
            text: ''
            size_hint_y: None
            height: dp(40)
            font_size: '11sp'
            bold: True
            color: (0.05, 0.25, 0.55, 1)
            halign: 'center'
            text_size: self.width - 12, None

        BoxLayout:
            orientation: 'vertical'
            size_hint_y: None
            height: dp(96)
            padding: 14
            spacing: 4
            canvas.before:
                Color:
                    rgba: 1, 1, 1, 1
                RoundedRectangle:
                    pos: self.pos
                    size: self.size
                    radius: [20]
                Color:
                    rgba: 0, 0, 0, 1
                Line:
                    width: 3.5
                    rounded_rectangle: (self.x, self.y, self.width, self.height, 20)

            Label:
                text: 'READY TO RUMBLE?'
                font_size: '12sp'
                bold: True
                color: (0.25, 0.25, 0.28, 1)
                halign: 'center'
                size_hint_y: None
                height: dp(22)

            Label:
                text: 'TAP ANY TOPIC BELOW TO START!'
                font_size: '20sp'
                bold: True
                color: (0, 0, 0, 1)
                halign: 'center'
                valign: 'middle'
                text_size: self.width - 12, None
                size_hint_y: None
                height: dp(54)

        ScrollView:
            size_hint_y: 1
            do_scroll_y: True
            bar_width: dp(8)
            BoxLayout:
                orientation: 'vertical'
                spacing: dp(10)
                padding: [6, 6]
                size_hint_y: None
                height: self.minimum_height

                ComicOutlineButton:
                    text: 'GENERAL KNOWLEDGE'
                    font_size: '17sp'
                    size_hint_y: None
                    height: dp(56)
                    on_release:
                        app.root.get_screen('difficulty').set_category('General Knowledge')
                        app.root.current = 'difficulty'

                ComicOutlineButton:
                    text: 'SCIENCE'
                    font_size: '17sp'
                    size_hint_y: None
                    height: dp(56)
                    on_release:
                        app.root.get_screen('difficulty').set_category('Science')
                        app.root.current = 'difficulty'

                ComicOutlineButton:
                    text: 'TECHNOLOGY'
                    font_size: '17sp'
                    size_hint_y: None
                    height: dp(56)
                    on_release:
                        app.root.get_screen('difficulty').set_category('Technology')
                        app.root.current = 'difficulty'

                ComicOutlineButton:
                    text: 'MOVIES & ENTERTAINMENT'
                    font_size: '17sp'
                    size_hint_y: None
                    height: dp(56)
                    on_release:
                        app.root.get_screen('difficulty').set_category('Movies & Entertainment')
                        app.root.current = 'difficulty'

                ComicOutlineButton:
                    text: 'SPORTS'
                    font_size: '17sp'
                    size_hint_y: None
                    height: dp(56)
                    on_release:
                        app.root.get_screen('difficulty').set_category('Sports')
                        app.root.current = 'difficulty'

                ComicOutlineButton:
                    text: 'MATHEMATICS'
                    font_size: '17sp'
                    size_hint_y: None
                    height: dp(56)
                    on_release:
                        app.root.get_screen('difficulty').set_category('Mathematics')
                        app.root.current = 'difficulty'

                ComicOutlineButton:
                    text: 'GEOGRAPHY'
                    font_size: '17sp'
                    size_hint_y: None
                    height: dp(56)
                    on_release:
                        app.root.get_screen('difficulty').set_category('Geography')
                        app.root.current = 'difficulty'

                ComicOutlineButton:
                    text: 'PHILIPPINE TRIVIA'
                    font_size: '17sp'
                    size_hint_y: None
                    height: dp(56)
                    on_release:
                        app.root.get_screen('difficulty').set_category('Philippine Trivia')
                        app.root.current = 'difficulty'

                ComicOutlineButton:
                    text: 'ANIME'
                    font_size: '17sp'
                    size_hint_y: None
                    height: dp(56)
                    on_release:
                        app.root.get_screen('difficulty').set_category('Anime')
                        app.root.current = 'difficulty'

                ComicOutlineButton:
                    text: 'RIDDLES & BRAIN TEASERS'
                    font_size: '15sp'
                    size_hint_y: None
                    height: dp(56)
                    on_release:
                        app.root.get_screen('difficulty').set_category('Riddles & Brain Teasers')
                        app.root.current = 'difficulty'

        BoxLayout:
            size_hint_y: None
            height: dp(50)
            spacing: 6

            ComicOutlineButton:
                text: 'PROFILE'
                font_size: '11sp'
                height: dp(42)
                size_hint_x: 0.34
                on_release: app.root.current = 'profile'

            ComicOutlineButton:
                text: 'HOW TO'
                font_size: '11sp'
                height: dp(42)
                size_hint_x: 0.33
                on_release: root.show_instructions()

            ComicOutlineButton:
                text: 'EXIT'
                font_size: '12sp'
                bold: True
                height: dp(42)
                size_hint_x: 0.31
                background_color: (1, 0.88, 0.88, 1)
                color: (0.45, 0, 0, 1)
                on_release: app.stop()

        BoxLayout:
            size_hint_y: None
            height: dp(50)
            spacing: 8

            ComicOutlineButton:
                text: 'LEADERBOARD'
                font_size: '12sp'
                height: dp(42)
                size_hint_x: 0.5
                on_release: root.show_leaderboard()

            ComicOutlineButton:
                text: 'SETTINGS'
                font_size: '11sp'
                height: dp(42)
                size_hint_x: 0.5
                on_release: root.show_settings()

<DifficultyScreen>:
    BoxLayout:
        id: bg_primary
        orientation: 'vertical'
        padding: 18
        spacing: 12

        BoxLayout:
            orientation: 'vertical'
            size_hint_y: 0.16
            padding: 12
            spacing: 4
            canvas.before:
                Color:
                    rgba: 1, 1, 1, 1
                RoundedRectangle:
                    pos: self.pos
                    size: self.size
                    radius: [18]
                Color:
                    rgba: 0, 0, 0, 1
                Line:
                    width: 3
                    rounded_rectangle: (self.x, self.y, self.width, self.height, 18)

            Label:
                text: 'HOW TOUGH ARE YA?'
                font_size: '22sp'
                bold: True
                color: (0, 0, 0, 1)
                size_hint_y: None
                height: dp(30)

            Label:
                id: category_label
                text: ''
                font_size: '18sp'
                bold: True
                color: (0.2, 0.2, 0.22, 1)
                size_hint_y: None
                height: dp(28)

        GridLayout:
            cols: 1
            spacing: 12
            size_hint_y: 0.72
            padding: [8, 8]

            ComicOutlineButton:
                text: 'EASY'
                font_size: '24sp'
                height: dp(72)
                on_release: root.start_quiz('easy')

            ComicOutlineButton:
                text: 'MEDIUM'
                font_size: '24sp'
                height: dp(72)
                on_release: root.start_quiz('medium')

            ComicOutlineButton:
                text: 'HARD!'
                font_size: '24sp'
                height: dp(72)
                background_color: (1, 0.93, 0.93, 1)
                on_release: root.start_quiz('hard')

        NeutralButton:
            text: 'BACK'
            size_hint_y: None
            height: dp(52)
            on_release: app.root.current = 'category'

<QuizScreen>:
    BoxLayout:
        id: bg_primary
        orientation: 'vertical'
        padding: 14
        spacing: 8

        BoxLayout:
            size_hint_y: 0.12
            orientation: 'horizontal'
            spacing: 6

            ComicOutlineButton:
                id: pause_btn
                text: '||'
                font_size: '13sp'
                size_hint_x: 0.11
                height: dp(50)
                on_release: root.toggle_pause()

            BoxLayout:
                orientation: 'vertical'
                size_hint_x: 0.38
                spacing: 4

                Label:
                    id: counter_label
                    text: 'Q1/10'
                    font_size: '14sp'
                    bold: True
                    halign: 'left'
                    size_hint_y: 0.5
                    color: (0, 0, 0, 1)

                ProgressBar:
                    id: progress_bar
                    max: 10
                    value: 0
                    size_hint_y: 0.4

            Label:
                id: streak_label
                text: 'STREAK x0'
                size_hint_x: 0.20
                font_size: '11sp'
                bold: True
                color: (0.72, 0.35, 0, 1)
                halign: 'center'
                valign: 'middle'
                text_size: self.width - 4, None

            BoxLayout:
                orientation: 'vertical'
                size_hint_x: 0.21
                spacing: 4

                Label:
                    id: timer_label
                    text: 'TIME: 30s'
                    font_size: '12sp'
                    bold: True
                    halign: 'right'
                    size_hint_y: 0.5
                    color: (0, 0, 0, 1)

                ProgressBar:
                    id: timer_bar
                    max: 100
                    value: 100
                    size_hint_y: 0.4

            ComicOutlineButton:
                text: 'X'
                size_hint_x: 0.10
                font_size: '15sp'
                height: dp(50)
                background_color: (1, 0.92, 0.92, 1)
                color: (0.45, 0, 0, 1)
                on_release: root.exit_to_category()

        Label:
            id: category_label
            text: ''
            size_hint_y: 0.05
            font_size: '13sp'
            bold: True
            color: (0, 0, 0, 1)

        BoxLayout:
            id: question_card
            size_hint_y: 0.26
            padding: 18
            canvas.before:
                Color:
                    rgba: (1, 1, 1, 1)
                RoundedRectangle:
                    pos: self.pos
                    size: self.size
                    radius: [22]
                Color:
                    rgba: 0, 0, 0, 1
                Line:
                    width: 3.5
                    rounded_rectangle: (self.x, self.y, self.width, self.height, 22)

            Label:
                id: question_label
                text: ''
                font_size: '19sp'
                bold: True
                halign: 'center'
                valign: 'middle'
                text_size: self.width - 20, None
                color: (0, 0, 0, 1)

        Label:
            id: score_label
            text: 'SCORE: 0'
            size_hint_y: 0.06
            font_size: '17sp'
            bold: True
            padding: [8, 3]
            color: (0, 0, 0, 1)

        Label:
            id: feedback_label
            text: ''
            size_hint_y: 0.10
            font_size: '11sp'
            bold: True
            color: (0.05, 0.55, 0.2, 1)
            halign: 'center'
            valign: 'middle'
            text_size: self.width - 16, None

        ScrollView:
            size_hint_y: 0.41
            do_scroll_y: True
            BoxLayout:
                id: answers_box
                orientation: 'vertical'
                spacing: 12
                size_hint_y: None
                height: self.minimum_height
                padding: [5, 5]

<ResultScreen>:
    BoxLayout:
        id: bg_primary
        orientation: 'vertical'
        padding: 18
        spacing: 12

        BoxLayout:
            orientation: 'vertical'
            size_hint_y: 0.14
            padding: 12
            spacing: 4
            canvas.before:
                Color:
                    rgba: 1, 1, 1, 1
                RoundedRectangle:
                    pos: self.pos
                    size: self.size
                    radius: [18]
                Color:
                    rgba: 0, 0, 0, 1
                Line:
                    width: 3.5
                    rounded_rectangle: (self.x, self.y, self.width, self.height, 18)

            Label:
                text: 'GAME OVER!'
                font_size: '26sp'
                bold: True
                color: (0, 0, 0, 1)
                halign: 'center'

            Label:
                text: '★ THE FINAL SCORE ★'
                font_size: '11sp'
                bold: True
                color: (0.28, 0.28, 0.32, 1)
                halign: 'center'

        Label:
            id: win_label
            text: ''
            size_hint_y: 0.14
            font_size: '30sp'
            bold: True
            halign: 'center'
            valign: 'middle'
            color: (0, 0, 0, 1)

        BoxLayout:
            size_hint_y: 0.22
            orientation: 'vertical'
            padding: 14
            spacing: 6
            canvas.before:
                Color:
                    rgba: 1, 1, 1, 1
                RoundedRectangle:
                    pos: self.pos
                    size: self.size
                    radius: [24]
                Color:
                    rgba: 0, 0, 0, 1
                Line:
                    width: 4
                    rounded_rectangle: (self.x, self.y, self.width, self.height, 24)

            Label:
                text: 'YOUR SCORE'
                font_size: '13sp'
                bold: True
                color: (0.3, 0.3, 0.35, 1)
                halign: 'center'

            Label:
                id: score_label
                text: '0/10'
                font_size: '52sp'
                bold: True
                color: (0, 0, 0, 1)
                halign: 'center'

        Label:
            id: message_label
            text: ''
            size_hint_y: 0.09
            font_size: '17sp'
            bold: True
            halign: 'center'
            valign: 'middle'
            text_size: self.width - 28, None

        Label:
            id: time_label
            text: ''
            size_hint_y: 0.042
            font_size: '13sp'
            bold: True
            color: (0.18, 0.18, 0.22, 1)

        Label:
            id: streak_result_label
            text: ''
            size_hint_y: 0.042
            font_size: '12sp'
            bold: True
            color: (0.55, 0.25, 0, 1)
            halign: 'center'

        BoxLayout:
            orientation: 'vertical'
            spacing: 10
            size_hint_y: 0.24

            ComicHeroButton:
                text: 'REMATCH!'
                font_size: '18sp'
                height: dp(56)
                on_release: root.retry_category()

            ComicOutlineButton:
                text: 'NEW TOPIC'
                font_size: '16sp'
                height: dp(52)
                on_release: root.play_again()

            DangerButton:
                text: 'EXIT'
                on_release: app.stop()

<ProfileScreen>:
    BoxLayout:
        id: bg_primary
        orientation: 'vertical'
        padding: 16
        spacing: 10

        Label:
            text: 'MY PROFILE!'
            size_hint_y: 0.08
            font_size: '26sp'
            bold: True
            color: (0, 0, 0, 1)

        Label:
            id: username_label
            text: ''
            size_hint_y: 0.06
            font_size: '20sp'
            bold: True
            color: (0, 0, 0, 1)

        ScrollView:
            size_hint_y: 0.76
            do_scroll_y: True
            BoxLayout:
                orientation: 'vertical'
                size_hint_y: None
                height: self.minimum_height
                spacing: 10
                padding: [4, 4]

                GridLayout:
                    cols: 2
                    size_hint_y: None
                    height: dp(180)
                    spacing: 8

                    BoxLayout:
                        orientation: 'vertical'
                        padding: [10, 8]
                        canvas.before:
                            Color:
                                rgba: (1, 1, 1, 1)
                            RoundedRectangle:
                                pos: self.pos
                                size: self.size
                                radius: [12]
                            Color:
                                rgba: 0, 0, 0, 1
                            Line:
                                width: 2.5
                                rounded_rectangle: (self.x, self.y, self.width, self.height, 12)
                        Label:
                            text: 'Games'
                            size_hint_y: 0.45
                            font_size: '11sp'
                            color: (0.45, 0.45, 0.5, 1)
                        Label:
                            id: total_games_value
                            text: '0'
                            bold: True
                            font_size: '22sp'
                            color: (0, 0, 0, 1)

                    BoxLayout:
                        orientation: 'vertical'
                        padding: [10, 8]
                        canvas.before:
                            Color:
                                rgba: (1, 1, 1, 1)
                            RoundedRectangle:
                                pos: self.pos
                                size: self.size
                                radius: [12]
                            Color:
                                rgba: 0, 0, 0, 1
                            Line:
                                width: 2.5
                                rounded_rectangle: (self.x, self.y, self.width, self.height, 12)
                        Label:
                            text: 'Best Score'
                            size_hint_y: 0.45
                            font_size: '11sp'
                            color: (0.45, 0.45, 0.5, 1)
                        Label:
                            id: best_score_value
                            text: '0/10'
                            bold: True
                            font_size: '22sp'
                            color: (0, 0, 0, 1)

                    BoxLayout:
                        orientation: 'vertical'
                        padding: [10, 8]
                        canvas.before:
                            Color:
                                rgba: (1, 1, 1, 1)
                            RoundedRectangle:
                                pos: self.pos
                                size: self.size
                                radius: [12]
                            Color:
                                rgba: 0, 0, 0, 1
                            Line:
                                width: 2.5
                                rounded_rectangle: (self.x, self.y, self.width, self.height, 12)
                        Label:
                            text: 'Average'
                            size_hint_y: 0.45
                            font_size: '11sp'
                            color: (0.45, 0.45, 0.5, 1)
                        Label:
                            id: avg_score_value
                            text: '0.0/10'
                            bold: True
                            font_size: '22sp'
                            color: (0, 0, 0, 1)

                    BoxLayout:
                        orientation: 'vertical'
                        padding: [10, 8]
                        canvas.before:
                            Color:
                                rgba: (1, 1, 1, 1)
                            RoundedRectangle:
                                pos: self.pos
                                size: self.size
                                radius: [12]
                            Color:
                                rgba: 0, 0, 0, 1
                            Line:
                                width: 2.5
                                rounded_rectangle: (self.x, self.y, self.width, self.height, 12)
                        Label:
                            text: 'Completion'
                            size_hint_y: 0.45
                            font_size: '11sp'
                            color: (0.45, 0.45, 0.5, 1)
                        Label:
                            id: completion_value
                            text: '0%'
                            bold: True
                            font_size: '22sp'
                            color: (0, 0, 0, 1)

                BoxLayout:
                    orientation: 'vertical'
                    size_hint_y: None
                    height: self.minimum_height
                    padding: [10, 10]
                    spacing: 6
                    canvas.before:
                        Color:
                            rgba: (1, 1, 1, 1)
                        RoundedRectangle:
                            pos: self.pos
                            size: self.size
                            radius: [12]
                        Color:
                            rgba: 0, 0, 0, 1
                        Line:
                            width: 2.5
                            rounded_rectangle: (self.x, self.y, self.width, self.height, 12)
                    Label:
                        text: 'DIFFICULTY PERFORMANCE'
                        size_hint_y: None
                        height: dp(22)
                        bold: True
                        font_size: '12sp'
                        color: (0, 0, 0, 1)
                    BoxLayout:
                        id: difficulty_box
                        orientation: 'vertical'
                        size_hint_y: None
                        height: self.minimum_height
                        spacing: 4

                BoxLayout:
                    orientation: 'vertical'
                    size_hint_y: None
                    height: self.minimum_height
                    padding: [10, 10]
                    spacing: 6
                    canvas.before:
                        Color:
                            rgba: (1, 1, 1, 1)
                        RoundedRectangle:
                            pos: self.pos
                            size: self.size
                            radius: [12]
                        Color:
                            rgba: 0, 0, 0, 1
                        Line:
                            width: 2.5
                            rounded_rectangle: (self.x, self.y, self.width, self.height, 12)
                    Label:
                        text: 'CATEGORY PERFORMANCE'
                        size_hint_y: None
                        height: dp(22)
                        bold: True
                        font_size: '12sp'
                        color: (0, 0, 0, 1)
                    BoxLayout:
                        id: category_box
                        orientation: 'vertical'
                        size_hint_y: None
                        height: self.minimum_height
                        spacing: 3

                BoxLayout:
                    orientation: 'vertical'
                    size_hint_y: None
                    height: self.minimum_height
                    padding: [10, 10]
                    spacing: 4
                    canvas.before:
                        Color:
                            rgba: (1, 1, 1, 1)
                        RoundedRectangle:
                            pos: self.pos
                            size: self.size
                            radius: [12]
                        Color:
                            rgba: 0, 0, 0, 1
                        Line:
                            width: 2.5
                            rounded_rectangle: (self.x, self.y, self.width, self.height, 12)
                    Label:
                        text: 'ACHIEVEMENTS'
                        size_hint_y: None
                        height: dp(22)
                        bold: True
                        font_size: '12sp'
                        color: (0, 0, 0, 1)
                    BoxLayout:
                        id: achievements_box
                        orientation: 'vertical'
                        size_hint_y: None
                        height: self.minimum_height
                        spacing: 2

                BoxLayout:
                    orientation: 'vertical'
                    size_hint_y: None
                    height: self.minimum_height
                    padding: [10, 10]
                    spacing: 4
                    canvas.before:
                        Color:
                            rgba: (1, 1, 1, 1)
                        RoundedRectangle:
                            pos: self.pos
                            size: self.size
                            radius: [12]
                        Color:
                            rgba: 0, 0, 0, 1
                        Line:
                            width: 2.5
                            rounded_rectangle: (self.x, self.y, self.width, self.height, 12)
                    Label:
                        text: 'RECENT GAMES'
                        size_hint_y: None
                        height: dp(22)
                        bold: True
                        font_size: '12sp'
                        color: (0, 0, 0, 1)
                    BoxLayout:
                        id: recent_box
                        orientation: 'vertical'
                        size_hint_y: None
                        height: self.minimum_height
                        spacing: 3

        BoxLayout:
            size_hint_y: 0.08
            spacing: 10

            DangerButton:
                text: 'LOGOUT'
                size_hint_x: 0.5
                on_release: root.logout()

            PrimaryButton:
                text: 'BACK'
                size_hint_x: 0.5
                on_release: root.back_to_game()
        '''

        from kivy.lang import Builder
        Builder.load_string(kv_string)

        sm = kivy.uix.screenmanager.ScreenManager()

        login_screen = LoginScreen(name='login')
        register_screen = RegisterScreen(name='register')
        profile_screen = ProfileScreen(name='profile')
        category_screen = CategoryScreen(name='category')
        difficulty_screen = DifficultyScreen(name='difficulty')
        quiz_screen = QuizScreen(name='quiz')
        result_screen = ResultScreen(name='result')

        login_screen.theme_manager = self.theme_manager
        login_screen.user_manager = self.user_manager

        register_screen.theme_manager = self.theme_manager
        register_screen.user_manager = self.user_manager

        profile_screen.theme_manager = self.theme_manager
        profile_screen.user_manager = self.user_manager

        category_screen.theme_manager = self.theme_manager
        category_screen.user_manager = self.user_manager

        difficulty_screen.theme_manager = self.theme_manager
        quiz_screen.theme_manager = self.theme_manager
        quiz_screen.user_manager = self.user_manager
        result_screen.theme_manager = self.theme_manager

        sm.add_widget(login_screen)
        sm.add_widget(register_screen)
        sm.add_widget(profile_screen)
        sm.add_widget(category_screen)
        sm.add_widget(difficulty_screen)
        sm.add_widget(quiz_screen)
        sm.add_widget(result_screen)

        return sm

    def toggle_theme(self):
        colors = self.theme_manager.toggle_theme()
        if self.root:
            for screen in self.root.screens:
                if hasattr(screen, 'apply_theme'):
                    try:
                        screen.apply_theme(colors)
                    except:
                        pass


if __name__ == '__main__':
    QuizApp().run()