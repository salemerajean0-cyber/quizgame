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
import questions
import os

# Set window size
kivy.core.window.Window.size = (400, 600)


class CategoryScreen(kivy.uix.screenmanager.Screen):
    pass


class QuizScreen(kivy.uix.screenmanager.Screen):
    question_num = kivy.properties.NumericProperty(0)
    score = kivy.properties.NumericProperty(0)
    category = kivy.properties.StringProperty("")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.questions = []
        self.current_question = None
        self.answered = False

    def start_quiz(self, category):
        """Start quiz with selected category"""
        self.category = category
        self.questions = questions.questions_data[category]
        self.question_num = 0
        self.score = 0
        self.answered = False
        self.display_question()

    def display_question(self):
        """Display current question"""
        if self.question_num < len(self.questions):
            self.current_question = self.questions[self.question_num]

            # Update question text
            self.ids.question_label.text = self.current_question.question
            self.ids.counter_label.text = f"Question {self.question_num + 1}/10"
            self.ids.score_label.text = f"Score: {self.score}"

            # Clear previous answer buttons
            self.ids.answers_box.clear_widgets()

            # Create answer buttons
            for option in self.current_question.options:
                btn = kivy.uix.button.Button(
                    text=option,
                    size_hint_y=None,
                    height=kivy.metrics.dp(50),
                    background_normal='',
                    background_color=(0.3, 0.5, 0.9, 1),
                    color=(1, 1, 1, 1)
                )
                btn.bind(on_release=self.check_answer)
                self.ids.answers_box.add_widget(btn)

    def check_answer(self, instance):
        """Check if answer is correct"""
        if self.answered:
            return

        self.answered = True

        # Check answer
        if instance.text == self.current_question.correct_answer:
            instance.background_color = (0, 1, 0, 1)  # Green
            self.score += 1
            self.ids.score_label.text = f"Score: {self.score}"
        else:
            instance.background_color = (1, 0, 0, 1)  # Red
            # Highlight correct answer
            for child in self.ids.answers_box.children:
                if child.text == self.current_question.correct_answer:
                    child.background_color = (0, 1, 0, 1)

        # Disable all buttons
        for child in self.ids.answers_box.children:
            child.disabled = True

        # Move to next question or show results
        if self.question_num < len(self.questions) - 1:
            kivy.clock.Clock.schedule_once(self.next_question, 1)
        else:
            kivy.clock.Clock.schedule_once(self.show_results, 1)

    def next_question(self, dt):
        """Go to next question"""
        self.question_num += 1
        self.answered = False
        self.display_question()

    def show_results(self, dt):
        """Show final results"""
        self.manager.get_screen('result').display_results(self.score, self.category)
        self.manager.current = 'result'

    def exit_to_category(self):
        """Exit quiz and return to category selection"""
        self.show_exit_confirmation()

    def show_exit_confirmation(self):
        """Show confirmation dialog before exiting"""
        content = kivy.uix.boxlayout.BoxLayout(orientation='vertical', spacing=10, padding=10)
        content.add_widget(kivy.uix.label.Label(
            text='Are you sure you want to exit?\nYour progress will be lost!',
            halign='center'
        ))

        buttons = kivy.uix.boxlayout.BoxLayout(size_hint_y=0.4, spacing=10)

        yes_btn = kivy.uix.button.Button(text='Yes, Exit', background_color=(0.8, 0.3, 0.3, 1))
        yes_btn.bind(on_release=lambda x: self.confirm_exit())

        no_btn = kivy.uix.button.Button(text='No, Continue', background_color=(0.3, 0.7, 0.5, 1))
        no_btn.bind(on_release=self.dismiss_popup)

        buttons.add_widget(yes_btn)
        buttons.add_widget(no_btn)
        content.add_widget(buttons)

        self.exit_popup = kivy.uix.popup.Popup(
            title='Exit Quiz',
            content=content,
            size_hint=(0.8, 0.4),
            auto_dismiss=False
        )

        self.exit_popup.open()

    def dismiss_popup(self, instance):
        """Dismiss the exit confirmation popup"""
        if hasattr(self, 'exit_popup'):
            self.exit_popup.dismiss()

    def confirm_exit(self):
        """Confirm exit and return to category screen"""
        self.dismiss_popup(None)
        self.reset_quiz()
        self.manager.current = 'category'

    def reset_quiz(self):
        """Reset quiz state"""
        self.question_num = 0
        self.score = 0
        self.answered = False
        self.ids.counter_label.text = "Question 1/10"
        self.ids.score_label.text = "Score: 0"
        self.ids.question_label.text = ""
        self.ids.answers_box.clear_widgets()


class ResultScreen(kivy.uix.screenmanager.Screen):
    def display_results(self, score, category):
        """Display quiz results"""
        self.ids.score_label.text = f"Your Score: {score}/10"

        # Message based on score - WITHOUT EMOJIS
        if score == 10:
            message = "Perfect! You're a genius! (Excellent)"
        elif score >= 8:
            message = "Great job! Well done! (Very Good)"
        elif score >= 6:
            message = "Good effort! Keep learning! (Good)"
        else:
            message = "Keep practicing! You'll do better next time! (Keep Trying)"

        self.ids.message_label.text = message
        self.category = category

    def play_again(self):
        """Go back to category selection"""
        self.manager.current = 'category'

    def retry_category(self):
        """Retry same category"""
        quiz_screen = self.manager.get_screen('quiz')
        quiz_screen.start_quiz(self.category)
        self.manager.current = 'quiz'

    def exit_to_categories(self):
        """Exit to category screen"""
        self.manager.current = 'category'


class QuizApp(kivy.app.App):
    def build(self):
        self.title = "Quiz Game"

        # Create screen manager
        sm = kivy.uix.screenmanager.ScreenManager()
        sm.add_widget(CategoryScreen(name='category'))
        sm.add_widget(QuizScreen(name='quiz'))
        sm.add_widget(ResultScreen(name='result'))

        return sm


if __name__ == '__main__':
    QuizApp().run()