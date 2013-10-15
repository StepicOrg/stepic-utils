import numbers
import os
import types
import sys
import importlib
import traceback
import itertools
import unittest
from functools import wraps
from inspect import signature

from . import utils


def import_module(path):
    path = os.path.abspath(path)
    module_dir = os.path.dirname(path)
    module_name = os.path.basename(path).replace('.py', '')
    sys.path.insert(0, module_dir)
    module = importlib.import_module(module_name)
    return module


def check_signatures(specs):
    """
    specs: [(name, function, expected number of args)]
    """
    for name, f, n_param in specs:
        if not callable(f):
            fail_with_message("`{}` is not callable.".format(name))
        s = signature(f)
        if len(s.parameters) != n_param:
            msg = "Expected `{}` with {} arguments, got {}."
            parms = ', '.join(s.parameters.keys()) if s.parameters else "none"
            fail_with_message(msg.format(name, n_param, parms))


class BaseQuiz(object):

    def __init__(self, module, generate_fun, solve_fun, check_fun):
        self.module = module
        self.generate = self.wrap_generate(generate_fun)
        self.solve = self.wrap_solve(solve_fun)
        self.check = self.wrap_check(check_fun)

    @classmethod
    def import_quiz(cls, path_or_module):
        """
        Loads quiz from module specified by path.
        Module should export `generate`, `solve` and `check`
        """
        if isinstance(path_or_module, str):
            module = import_module(path_or_module)
        else:
            assert isinstance(path_or_module, types.ModuleType)
            module = path_or_module

        no_function_msg = "Can't export `{}` from quiz module.\nQuiz should export {}."
        attrs = ['solve', 'check']
        for attr in attrs:
            if not hasattr(module, attr):
                fail_with_message(no_function_msg.format(attr, ', '.join(attrs)))

        generate = getattr(module, 'generate', None)
        solve = getattr(module, 'solve')
        check = getattr(module, 'check')
        return cls(module, generate, solve, check)

    @classmethod
    def load_tests(cls, module):
        return QuizModuleTest(cls, module)

    @classmethod
    def get_test_loader(cls):
        return QuizTestLoader(cls)

    def wrap_generate(self, generate):
        @wraps(generate)
        def f():
            ret = call_user_code(generate)
            if isinstance(ret, tuple):
                try:
                    dataset, clue = ret
                except ValueError:
                    fail_with_message("generate() returned a tuple but it's length is not 2.\n"
                                      "generate should return either a dataset "
                                      "or a (dataset, clue) tuple.")
                    assert False
            else:
                dataset = ret
                clue = self.solve(dataset)
            dataset = self.clean_dataset(dataset)
            clue = self.clean_clue(clue)
            return dataset, clue
        return f

    def wrap_solve(self, solve):
        @wraps(solve)
        def f(dataset):
            if isinstance(dataset, dict) and 'file' in dataset and len(dataset) == 1:
                dataset = dataset['file']
            return self.clean_answer(call_user_code(solve, dataset))
        return f

    def wrap_check(self, check):
        @wraps(check)
        def f(reply, clue):
            ret = call_user_code(check, reply, clue)
            if isinstance(ret, tuple):
                try:
                    score_value, hint = ret
                except ValueError:
                    fail_with_message("check() returned a tuple but it's length is not 2.\n"
                                      "check should return either a score or a (score, hit) tuple.")
                    assert False
            else:
                score_value = ret
                hint = ''
            score_value = self.clean_score(score_value)
            hint = self.clean_hint(hint)
            return score_value, hint
        return f

    @staticmethod
    def clean_dataset(dataset):
        if not isinstance(dataset, (dict, str, bytes)):
            msg = "dataset should be one of (dict, str, bytes) instead of {}"
            fail_with_message(msg.format(dataset))

        if isinstance(dataset, (str, bytes)):
            dataset = {'file': dataset}
        return dataset

    @staticmethod
    def clean_clue(clue):
        try:
            cleaned_clue = utils.decode(utils.encode(clue))
            return cleaned_clue
        except (TypeError, ValueError):
            msg = "clue is not serializable: {}"
            fail_with_message(msg.format(clue))

    @staticmethod
    def clean_answer(answer):
        if not isinstance(answer, str):
            msg = "answer should be a str instead of {}"
            fail_with_message(msg.format(answer))
        return answer

    @staticmethod
    def clean_score(score):
        if not (isinstance(score, numbers.Real) and (0.0 <= score <= 1.0)):
            fail_with_message("score should be a number in range [0, 1]")
        return score

    @staticmethod
    def clean_hint(hint):
        if not isinstance(hint, str):
            fail_with_message("hint should be a str")
        return hint


class DatasetQuiz(BaseQuiz):

    def __init__(self, module, generate_fun, solve_fun, check_fun):
        if generate_fun is None:
            check_signatures([("solve", solve_fun, 0),
                              ("check", check_fun, 1)])
            generate = lambda: ({}, '')
            solve = lambda dataset: solve_fun()
            check = lambda reply, clue: check_fun(reply)
        else:
            check_signatures([("generate", generate_fun, 0),
                              ("solve", solve_fun, 1),
                              ("check", check_fun, 2)])
            generate, solve, check = generate_fun, solve_fun, check_fun
        super().__init__(module, generate, solve, check)

    def self_check(self):
        dataset, clue = self.generate()
        answer = self.solve(dataset)
        score, hint = self.check(answer, clue)
        return score == 1


class CodeQuiz(BaseQuiz):
    def __init__(self, module, generate_fun, solve_fun, check_fun):
        if generate_fun is None:
            fail_with_message("Code Quiz should export generate")
        check_signatures([("generate", generate_fun, 0),
                          ("solve", solve_fun, 1),
                          ("check", check_fun, 2)])
        super().__init__(module, generate_fun, solve_fun, check_fun)

    def wrap_generate(self, generate):
        @wraps(generate)
        def f():
            ret = call_user_code(generate)
            if not isinstance(ret, list):
                fail_with_message("generate() should return a list instead of {}".format(ret))

            def is_dataset(x):
                return isinstance(x, str)

            def is_dataset_and_clue(x):
                return isinstance(x, tuple) and len(x) == 2 and isinstance(x[0], str)

            if all(map(is_dataset, ret)):
                return [(self.clean_dataset(dataset), self.clean_clue(self.solve(dataset)))
                        for dataset in ret]
            elif all(map(is_dataset_and_clue, ret)):
                return [(self.clean_dataset(dataset), self.clean_clue(clue))
                        for dataset, clue in ret]
            else:
                fail_with_message("generate() should return list of dataset or list of pairs "
                                  "(dataset, clue) instead of {}".format(ret))
        return f

    @staticmethod
    def clean_dataset(dataset):
        if not isinstance(dataset, str):
            fail_with_message("dataset should be a str instead of {}".format(dataset))
        return dataset

    def self_check(self):
        def is_correct(dataset, clue):
            answer = self.solve(dataset)
            score, hint = self.check(answer, clue)
            return score == 1
        test_cases = self.generate()
        return all(itertools.starmap(is_correct, test_cases))


class QuizTestLoader(unittest.TestLoader):
    def __init__(self, quiz_cls):
        self.quiz_cls = quiz_cls
        super().__init__()

    def loadTestsFromModule(self, module, use_load_tests=True):
        suite = super().loadTestsFromModule(module, use_load_tests)
        suite.addTest(self.quiz_cls.load_tests(module))
        return suite


class QuizModuleTest(unittest.TestCase):
    def __init__(self, quiz_cls, module, methodName='runTest'):
        super().__init__(methodName)
        self.quiz = quiz_cls.import_quiz(module)
        self.tests = getattr(module, 'tests', [])

    def runTest(self):
        msg = "tests should be a list of 3-tuples of strings: [(dataset, clue, reply)]"
        self.assertIsInstance(self.tests, list, msg)
        for test in self.tests:
            self.assertIsInstance(test, tuple, msg)
            self.assertEqual(len(test), 3, msg)
            dataset, clue, reply = test
            self.assertIsInstance(dataset, str, "dataset should be a string")
            self.assertIsInstance(reply, str, "reply should be a string")

        self.testSamples()
        self.testSolve()

    def testSamples(self):
        for dataset, clue, reply in self.tests:
            msg = "\nscore(reply, clue) != 1!\nscore({}, {}) == {}"
            score, _ = self.quiz.check(reply, clue)
            self.assertEqual(score, 1, msg.format(reply, clue, score))

    def testSolve(self):
        for dataset, clue, reply in self.tests:
            computed_reply = self.quiz.solve(dataset)
            msg = "\nscore(solve(dataset), clue) != 1!\nscore({}, {}) == {}"
            score, _ = self.quiz.check(computed_reply, clue)
            self.assertEqual(score, 1, msg.format(computed_reply, clue, score))


def fail_with_message(message):
    print(message, file=sys.stderr)
    sys.exit(-1)


def call_user_code(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except:
        traceback.print_exc()
        fail_with_message("Quiz failed with exception!")