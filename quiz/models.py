import re
import json

from django.db import models
from django.urls import reverse
from django.core.exceptions import ValidationError, ImproperlyConfigured
from django.core.validators import (MaxValueValidator, validate_comma_separated_integer_list,)
from django.utils.translation import gettext_lazy as _
from django.utils.timezone import now
from django.conf import settings
from django.db.models.signals import pre_save

from django.db.models import Q

from model_utils.managers import InheritanceManager
from course.models import Course
from .utils import *
from ckeditor_uploader.fields import RichTextUploadingField


CHOICE_ORDER_OPTIONS = (
    ('content', _('Контент')),
    ('random', _('Рандом')),
    ('none', _('Нет'))
)

CATEGORY_OPTIONS = (
    ('assignment', _('Задание')),
    ('exam', _('Экзамен')),
    ('practice', _('Практический тест'))
)


class QuizManager(models.Manager):
    def search(self, query=None):
        qs = self.get_queryset()
        if query is not None:
            or_lookup = (Q(title__icontains=query) | 
                         Q(description__icontains=query)|
                         Q(category__icontains=query)|
                         Q(slug__icontains=query)
                        )
            qs = qs.filter(or_lookup).distinct() # distinct() is often necessary with Q lookups
        return qs


class Quiz(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, null=True)
    title = models.CharField(verbose_name=_("Название"), max_length=60, blank=False)
    slug = models.SlugField(blank=True, unique=True)
    description = models.TextField(verbose_name=_("Описание"), blank=True, help_text=_("Описание теста"))
    category = models.TextField(choices=CATEGORY_OPTIONS, blank=True, verbose_name="Кагегория")
    random_order = models.BooleanField(blank=False, default=False, verbose_name=_("Случайный порядок"), 
        help_text=_("Отображать вопросы в случайном порядке или как они заданы?"))

    # max_questions = models.PositiveIntegerField(blank=True, null=True, verbose_name=_("Max Questions"), 
    #     help_text=_("Number of questions to be answered on each attempt."))

    answers_at_end = models.BooleanField(blank=False, default=False, verbose_name=_("Ответы в конце"),
        help_text=_("Правильный ответ НЕ отображается после вопроса. Ответы отображаются в конце."))

    exam_paper = models.BooleanField(blank=False, default=False, verbose_name=_("Экзаменационная бумага"),
        help_text=_("Если да, результат каждой попытки пользователя будет сохранен. Необходимо для маркировки."))

    single_attempt = models.BooleanField(blank=False, default=False, verbose_name=_("Единственная попытка"), 
        help_text=_("Если да, то пользователю будет разрешена только одна попытка."))

    pass_mark = models.SmallIntegerField(blank=True, default=50, verbose_name=_("Проходной балл"), validators=[MaxValueValidator(100)], 
        help_text=_("Процент, необходимый для сдачи экзамена."))

    draft = models.BooleanField(blank=True, default=False, verbose_name=_("Черновик"),
        help_text=_("Если да, тест не отображается в списке тестов, и его могут пройти только пользователи, которые могут редактировать тесты."))

    timestamp = models.DateTimeField(auto_now=True)

    objects = QuizManager()

    def save(self, force_insert=False, force_update=False, *args, **kwargs):

        if self.single_attempt is True:
            self.exam_paper = True

        if self.pass_mark > 100:
            raise ValidationError('%s Выше 100' % self.pass_mark)
        if self.pass_mark < 0:
            raise ValidationError('%s Меньше нуля' % self.pass_mark)

        super(Quiz, self).save(force_insert, force_update, *args, **kwargs)

    class Meta:
        verbose_name = _("Тест")
        verbose_name_plural = _("Тесты")

    def __str__(self):
        return self.title

    def get_questions(self):
        return self.question_set.all().select_subclasses()

    @property
    def get_max_score(self):
        return self.get_questions().count()

    def get_absolute_url(self):
        # return reverse('quiz_start_page', kwargs={'pk': self.pk})
        return reverse('quiz_index', kwargs={'slug': self.course.slug})


def quiz_pre_save_receiver(sender, instance, *args, **kwargs):
    if not instance.slug:
        instance.slug = unique_slug_generator(instance)

pre_save.connect(quiz_pre_save_receiver, sender=Quiz)


class ProgressManager(models.Manager):

    def new_progress(self, user):
        new_progress = self.create(user=user, score="")
        new_progress.save()
        return new_progress


class Progress(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, verbose_name=_("User"), on_delete=models.CASCADE)
    score = models.CharField(max_length=1024, verbose_name=_("Score"), validators=[validate_comma_separated_integer_list])

    objects = ProgressManager()

    class Meta:
        verbose_name = _("Прогресс пользователя")
        verbose_name_plural = _("Записи прогресса пользователя")

    # @property
    def list_all_cat_scores(self):
        score_before = self.score
        output = {}

        if len(self.score) > len(score_before):
            # If a new category has been added, save changes.
            self.save()

        return output

    def update_score(self, question, score_to_add=0, possible_to_add=0):
        # category_test = Category.objects.filter(category=question.category).exists()

        if any([item is False for item in [score_to_add, possible_to_add, isinstance(score_to_add, int), isinstance(possible_to_add, int)]]):
            return _("error"), _("категория не существует или недействительная оценка")

        to_find = re.escape(str(question.quiz)) + r",(?P<score>\d+),(?P<possible>\d+),"

        match = re.search(to_find, self.score, re.IGNORECASE)

        if match:
            updated_score = int(match.group('score')) + abs(score_to_add)
            updated_possible = int(match.group('possible')) + abs(possible_to_add)

            new_score = ",".join([str(question.quiz), str(updated_score), str(updated_possible), ""])

            # swap old score for the new one
            self.score = self.score.replace(match.group(), new_score)
            self.save()

        else:
            #  if not present but existing, add with the points passed in
            self.score += ",".join([str(question.quiz), str(score_to_add), str(possible_to_add), ""])
            self.save()

    def show_exams(self):
        if self.user.is_superuser:
            return Sitting.objects.filter(complete=True).order_by('-end')
        else:
            return Sitting.objects.filter(user=self.user, complete=True).order_by('-end')


class SittingManager(models.Manager):

    def new_sitting(self, user, quiz, course):
        if quiz.random_order is True:
            question_set = quiz.question_set.all().select_subclasses().order_by('?')
        else:
            question_set = quiz.question_set.all().select_subclasses()

        question_set = [item.id for item in question_set]

        if len(question_set) == 0:
            raise ImproperlyConfigured('Набор вопросов тестов пуст. Пожалуйста, сформулируйте вопросы правильно')

        # if quiz.max_questions and quiz.max_questions < len(question_set):
        #     question_set = question_set[:quiz.max_questions]

        questions = ",".join(map(str, question_set)) + ","

        new_sitting = self.create(
            user=user, quiz=quiz, course=course, question_order=questions, 
            question_list=questions, incorrect_questions="",
            current_score=0,
            complete=False,
            user_answers='{}'
        )
        return new_sitting

    def user_sitting(self, user, quiz, course):
        if quiz.single_attempt is True and self.filter(user=user, quiz=quiz, course=course, complete=True).exists():
            return False
        try:
            sitting = self.get(user=user, quiz=quiz, course=course, complete=False)
        except Sitting.DoesNotExist:
            sitting = self.new_sitting(user, quiz, course)
        except Sitting.MultipleObjectsReturned:
            sitting = self.filter(user=user, quiz=quiz, course=course, complete=False)[0]
        return sitting


class Sitting(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name=_("Пользватель"), on_delete=models.CASCADE)
    quiz = models.ForeignKey(Quiz, verbose_name=_("Тест"), on_delete=models.CASCADE)
    course = models.ForeignKey(Course, null=True, verbose_name=_("Курс"), on_delete=models.CASCADE)

    question_order = models.CharField(max_length=1024, verbose_name=_("Порядок вопросов"),
        validators=[validate_comma_separated_integer_list])

    question_list = models.CharField(max_length=1024, verbose_name=_("Список тестов"),
        validators=[validate_comma_separated_integer_list])

    incorrect_questions = models.CharField(max_length=1024, blank=True, verbose_name=_("Неправильные вопросы"),
        validators=[validate_comma_separated_integer_list])

    current_score = models.IntegerField(verbose_name=_("Текущий балл"))
    complete = models.BooleanField(default=False, blank=False, verbose_name=_("Закончен"))
    user_answers = models.TextField(blank=True, default='{}', verbose_name=_("Ответы пользователя"))
    start = models.DateTimeField(auto_now_add=True, verbose_name=_("Начать"))
    end = models.DateTimeField(null=True, blank=True, verbose_name=_("Завершить"))

    objects = SittingManager()

    class Meta:
        permissions = (("view_sittings", _("Можно посмотреть сданные экзамены.")),)

    def get_first_question(self):
        if not self.question_list:
            return False

        first, _ = self.question_list.split(',', 1)
        question_id = int(first)
        return Question.objects.get_subclass(id=question_id)

    def remove_first_question(self):
        if not self.question_list:
            return

        _, others = self.question_list.split(',', 1)
        self.question_list = others
        self.save()

    def add_to_score(self, points):
        self.current_score += int(points)
        self.save()

    @property
    def get_current_score(self):
        return self.current_score

    def _question_ids(self):
        return [int(n) for n in self.question_order.split(',') if n]

    @property
    def get_percent_correct(self):
        dividend = float(self.current_score)
        divisor = len(self._question_ids())
        if divisor < 1:
            return 0            # prevent divide by zero error

        if dividend > divisor:
            return 100

        correct = int(round((dividend / divisor) * 100))

        if correct >= 1:
            return correct
        else:
            return 0

    def mark_quiz_complete(self):
        self.complete = True
        self.end = now()
        self.save()

    def add_incorrect_question(self, question):
        if len(self.incorrect_questions) > 0:
            self.incorrect_questions += ','
        self.incorrect_questions += str(question.id) + ","
        if self.complete:
            self.add_to_score(-1)
        self.save()

    @property
    def get_incorrect_questions(self):
        return [int(q) for q in self.incorrect_questions.split(',') if q]

    def remove_incorrect_question(self, question):
        current = self.get_incorrect_questions
        current.remove(question.id)
        self.incorrect_questions = ','.join(map(str, current))
        self.add_to_score(1)
        self.save()

    @property
    def check_if_passed(self):
        return self.get_percent_correct >= self.quiz.pass_mark

    @property
    def result_message(self):
        if self.check_if_passed:
            return f"Вы прошли этот тест, поздравляю"
        else:
            return f"Тест провален"

    def add_user_answer(self, question, guess):
        current = json.loads(self.user_answers)
        current[question.id] = guess
        self.user_answers = json.dumps(current)
        self.save()

    def get_questions(self, with_answers=False):
        question_ids = self._question_ids()
        questions = sorted(self.quiz.question_set.filter(id__in=question_ids).select_subclasses(), key=lambda q: question_ids.index(q.id))

        if with_answers:
            user_answers = json.loads(self.user_answers)
            for question in questions:
                question.user_answer = user_answers[str(question.id)]

        return questions

    @property
    def questions_with_user_answers(self):
        return {q: q.user_answer for q in self.get_questions(with_answers=True)}

    @property
    def get_max_score(self):
        return len(self._question_ids())

    def progress(self):
        answered = len(json.loads(self.user_answers))
        total = self.get_max_score
        return answered, total


class Question(models.Model):
    quiz = models.ManyToManyField(Quiz, verbose_name=_("Тест"), blank=True)
    content = RichTextUploadingField( max_length=1000, blank=False, 
        help_text=_("Введите текст вопроса, который вы хотите отобразить"), verbose_name=_('Вопрос'))
    explanation = RichTextUploadingField(max_length=2000, blank=True,
        help_text=_("Объяснение, которое будет показано после ответа на вопрос."),
        verbose_name=_('Объяснение'))

    objects = InheritanceManager()

    class Meta:
        verbose_name = _("Вопрос")
        verbose_name_plural = _("Вопросы")

    def __str__(self):
        return self.content


class MCQuestion(Question):

    choice_order = models.CharField(
        max_length=30, null=True, blank=True,
        choices=CHOICE_ORDER_OPTIONS,
        help_text=_("Порядок, в котором варианты множественного выбора отображаются пользователю"),
        verbose_name=_("Порядок выбора"))

    def check_if_correct(self, guess):
        answer = Choice.objects.get(id=guess)

        if answer.correct is True:
            return True
        else:
            return False

    def order_choices(self, queryset):
        if self.choice_order == 'content':
            return queryset.order_by('choice')
        if self.choice_order == 'random':
            return queryset.order_by('?')
        if self.choice_order == 'none':
            return queryset.order_by()
        return queryset

    def get_choices(self):
        return self.order_choices(Choice.objects.filter(question=self))

    def get_choices_list(self):
        return [(choice.id, choice.choice) for choice in
                self.order_choices(Choice.objects.filter(question=self))]

    def answer_choice_to_string(self, guess):
        return Choice.objects.get(id=guess).choice

    class Meta:
        verbose_name = _("Вопрос с множественным выбором")
        verbose_name_plural = _("Вопросы с несколькими вариантами ответов")


class Choice(models.Model):
    question = models.ForeignKey(MCQuestion, verbose_name=_("Вопрос"), on_delete=models.CASCADE)

    choice = models.CharField(max_length=1000, blank=False,
        help_text=_("Введите текст выбора, который вы хотите отобразить"), 
        verbose_name=_("Контент"))

    correct = models.BooleanField(blank=False, default=False, 
        help_text=_("Это правильный ответ?"), 
        verbose_name=_("Правильный"))

    def __str__(self):
        return self.choice

    class Meta:
        verbose_name = _("Выбор")
        verbose_name_plural = _("Выбранные")


class Essay_Question(Question):

    def check_if_correct(self, guess):
        return False

    def get_answers(self):
        return False

    def get_answers_list(self):
        return False

    def answer_choice_to_string(self, guess):
        return str(guess)

    def __str__(self):
        return self.content

    class Meta:
        verbose_name = _("Вопрос в стиле эссе")
        verbose_name_plural = _("Вопросы в стиле эссе")
