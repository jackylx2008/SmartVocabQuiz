"""验证 skill 驱动的组合出题、答案隔离和判分。"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.request import Request, urlopen

import fitz


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from smartvocabquiz.flows.generate_quiz import (_build_items, _mask_word, _previous_answer_words,
                                                generate_quiz, grade_quiz, public_quiz)
from smartvocabquiz.web_server import AppState, _question_hash, make_server


WORDS = "apple banana pear red blue black rabbit tiger book chair pencil window school dog cat fish tree milk ruler duck yellow green small big animal pet bird monkey panda bear lion desk door family mother father and cute strong swim white".split()


class FakeQuizAI:
    model = "test-model"

    def __init__(self):
        self.calls = []
        self.checks = 0

    def check_available(self):
        self.checks += 1

    def generate_task(self, prompt, task_type, vocab_list, count, excluded, feedback):
        self.calls.append(task_type)
        assert task_type in prompt
        assert 'T7_MAKE_SENTENCES' not in prompt
        if task_type == "T11_DIALOGUE_REPLY":
            available = {item["english"] for item in vocab_list}
            pool = ("bear", "lion", "monkey", "panda", "rabbit", "bird", "book", "tree",
                    "chair", "window", "animal", "pet", "cat", "dog", "fish")
            choices = [word for word in pool if word in available and word not in excluded][:count]
            return {"task_type": task_type, "items": [{
                "scene_cn": f"你看到一只 {word}。", "target_word": word,
                "question": "Can I see it?",
                "options": [f"I can see a {word}."] + [f"I can see a {other}." for other in
                    ("dog", "cat", "bird", "fish") if other != word][:2],
                "answer": f"I can see a {word}.",
            } for word in choices]}
        if task_type == "T6_WORD_BANK_FILL":
            available = {item["english"] for item in vocab_list}
            pool = ("dog", "cat", "fish", "rabbit", "bear", "lion", "monkey", "panda", "bird")
            chosen = [word for word in pool if word in available and word not in excluded][:count]
            bank = chosen + [word for word in pool if word in available and word not in chosen][:2]
            return {"task_type": task_type, "word_bank": bank, "items": [{
                "sentence_with_blank": "I can see a ____.",
                "answer": word, "cn_translation": f"这是 {word}。",
            } for word in chosen]}
        if task_type == "T4_MISSING_LETTERS":
            available = {item["english"] for item in vocab_list}
            chosen = [word for word in ("book", "chair", "pencil", "window", "banana", "school", "family")
                      if word in available and word not in excluded][:count]
            return {"task_type": task_type, "items": [{
                "word": word, "cn_meaning": word,
                "masked_word": word[0] + "__" + word[3:], "missing_part": word[1:3],
            } for word in chosen]}
        if task_type == "T5_UNSCRAMBLE_WORDS":
            available = {item["english"] for item in vocab_list}
            chosen = [word for word in ("window", "banana", "pencil", "school", "family", "green", "black")
                      if word in available and word not in excluded][:count]
            return {"task_type": task_type, "items": [{
                "word": word, "cn_meaning": word, "phonics_hint": word,
            } for word in chosen]}
        data = {
            "T1_ODD_ONE_OUT": {"items": [
                {"options": ["apple", "banana", "pear", "red"], "answer": "red", "category_hint": "水果与颜色"},
                {"options": ["red", "blue", "black", "rabbit"], "answer": "rabbit", "category_hint": "颜色与动物"},
                {"options": ["dog", "cat", "fish", "tree"], "answer": "tree", "category_hint": "动物与植物"},
            ]},
            "T2_READ_AND_MATCH": {"items": [
                {"word": "tiger", "clue": "It is a big animal.", "cn_meaning": "老虎"},
                {"word": "bird", "clue": "It is a small animal.", "cn_meaning": "鸟"},
            ]},
        }[task_type]
        data["items"] = data["items"][:count]
        return {"task_type": task_type, **data}


class GenerateQuizTest(unittest.TestCase):
    def test_review_replaces_one_question_and_exports_selected_a4_pdf(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "output" / "vocabulary.json"
            output.parent.mkdir()
            output.write_text(json.dumps({"schema_version": 1, "entries": [
                {"english": word, "chinese": word} for word in WORDS]}), encoding="utf-8")
            ai = FakeQuizAI()
            state = AppState(root / "BookScan", output, root / "output" / "scan_manifest.json",
                             ROOT / "web", ai, scale=1, quality=80, retries=0)
            quiz = generate_quiz(output, ROOT / "docs" / "KIDS_VOCAB_QUIZ_MAKER.md",
                                 state.quiz_dir, ai)
            before = state.load_quiz(quiz["id"])
            state.review_question(quiz["id"], 6, "approve")
            state.review_question(quiz["id"], 7, "approve")
            replacement = state.review_question(quiz["id"], 7, "reject")["question"]
            after = state.load_quiz(quiz["id"])
            old_questions = [item for section in before["sections"] for item in section["questions"]]
            new_questions = [item for section in after["sections"] for item in section["questions"]]
            self.assertNotEqual(replacement["word"], old_questions[6]["word"])
            self.assertEqual([item for item in old_questions if item["number"] != 7],
                             [item for item in new_questions if item["number"] != 7])
            self.assertEqual(len(state.list_candidates()), 1)
            state.review_question(quiz["id"], 7, "approve")
            ids = [item["id"] for item in state.list_candidates()]
            self.assertTrue(all(re.fullmatch(r"[a-f0-9]{64}", item) for item in ids))
            state.select_candidates(ids)
            self.assertTrue(all(item["selected"] for item in state.list_candidates()))
            name = state.export_pdf()
            path = output.parent / name
            self.assertTrue(path.is_file())
            with fitz.open(path) as document:
                self.assertGreaterEqual(len(document), 1)
                self.assertAlmostEqual(document[0].rect.width, fitz.paper_rect("a4").height, delta=1)
                self.assertAlmostEqual(document[0].rect.height, fitz.paper_rect("a4").width, delta=1)
                text = "\n".join(page.get_text() for page in document)
                self.assertIn("共 2 题", text)
                self.assertNotIn("参考答案", text)
                english_spans = [span for page in document for block in page.get_text("dict")["blocks"]
                                 for line in block.get("lines", []) for span in line["spans"]
                                 if "Helvetica" in span["font"]]
                self.assertTrue(english_spans)
                self.assertGreaterEqual(max(span["size"] for span in english_spans), 18)
            restarted = AppState(root / "BookScan", output, root / "output" / "scan_manifest.json",
                                 ROOT / "web", ai, scale=1, quality=80, retries=0)
            self.assertEqual(len(restarted.list_candidates()), 2)
            self.assertEqual(len(restarted.review_snapshot(quiz["id"])["approved_ids"]), 2)
            other = json.loads(json.dumps(after))
            other["id"] = "20260102T000000-aaaaaaaa"
            (state.quiz_dir / f"{other['id']}.json").write_text(json.dumps(other), encoding="utf-8")
            reused = state.review_question(other["id"], 6, "approve")
            self.assertIn(reused["candidate_id"], ids)
            self.assertEqual(len(state.list_candidates()), 2)
            self.assertEqual(len(next(item for item in state.list_candidates()
                                      if item["id"] == reused["candidate_id"])["origins"]), 2)
            state.review_question(quiz["id"], 6, "reject")
            self.assertEqual(len(state.list_candidates()), 2)
            self.assertNotIn(6, state.review_snapshot(quiz["id"])["approved_ids"])
            self.assertIn(6, state.review_snapshot(other["id"])["approved_ids"])

    def test_existing_uuid_candidates_migrate_to_content_hash(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "output" / "vocabulary.json"
            output.parent.mkdir()
            legacy = {"id": "abb8aa55959d4fbe9d2b43933467a270",
                      "quiz_id": "20260101T000000-aaaaaaaa", "question_number": 4,
                      "section_title": "拼写巩固", "approved_at": "2026-01-01T00:00:00+00:00",
                      "selected": True,
                      "question": {"type": "T4_MISSING_LETTERS", "number": 4,
                                   "word": "book", "answer": "oo", "masked_word": "b__k"}}
            path = output.parent / "approved_questions.json"
            path.write_text(json.dumps({"schema_version": 1, "candidates": [legacy]}), encoding="utf-8")
            state = AppState(root / "BookScan", output, output.parent / "scan_manifest.json",
                             ROOT / "web", FakeQuizAI(), scale=1, quality=80, retries=0)
            migrated = state.list_candidates()[0]
            self.assertRegex(migrated["id"], r"^[a-f0-9]{64}$")
            self.assertEqual(_question_hash(legacy["question"]),
                             _question_hash({**legacy["question"], "number": 99}))
            self.assertNotEqual(_question_hash(legacy["question"]),
                                _question_hash({**legacy["question"], "word": "look"}))
            self.assertTrue(migrated["selected"])
            self.assertEqual(len(migrated["origins"]), 1)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["candidates"][0]["id"],
                             migrated["id"])

    def test_new_question_types_validate_and_shuffle(self):
        words = {word.casefold(): {"english": word, "chinese": word}
                 for word in ("cat", "dog", "bird", "fish", "chair", "window")}
        words["cat"]["chinese"] = "猫"
        picture = _build_items("T8_PICTURE_WORD_CHOICE", {
            "task_type": "T8_PICTURE_WORD_CHOICE", "items": [{
                "target_word": "cat", "options": ["cat", "dog", "bird", "fish"],
                "answer": "cat", "category_hint": "动物",
            }]}, 1, words, set())
        self.assertEqual(picture[0]["answer"], "cat")
        self.assertEqual(len(picture[0]["options"]), 4)
        matches = _build_items("T10_CN_EN_MATCH", {
            "task_type": "T10_CN_EN_MATCH", "items": [
                {"word": "chair", "cn_meaning": "椅子"},
                {"word": "window", "cn_meaning": "窗户"},
            ]}, 2, words, set())
        self.assertEqual(len(matches), 2)
        self.assertEqual(set(matches[0]["options"]), {"chair", "window"})
        reply = _build_items("T11_DIALOGUE_REPLY", {
            "task_type": "T11_DIALOGUE_REPLY", "items": [{
                "scene_cn": "你看见一只猫。", "target_word": "cat", "question": "Can I see it?",
                "options": ["I can see a dog.", "I can see a bird.", "I can see a cat."],
                "answer": "I can see a cat.",
            }]}, 1, words, set())
        self.assertEqual(reply[0]["answer"], "I can see a cat.")
        with self.assertRaisesRegex(ValueError, "塞进了问句"):
            _build_items("T11_DIALOGUE_REPLY", {
                "task_type": "T11_DIALOGUE_REPLY", "items": [{
                    "scene_cn": "朋友问这是什么猫。", "target_word": "cat",
                    "question": "What is this?",
                    "options": ["I can see a dog.", "I can see a bird.", "I can see a cat."],
                    "answer": "I can see a cat.",
                }]}, 1, words, set())

    def test_repairs_overmasked_words_and_unnatural_context(self):
        masked, missing = _mask_word("together", "t______r", "ogethe")
        self.assertEqual(masked.count("_"), 2)
        self.assertFalse(masked.startswith("_"))
        self.assertFalse(masked.endswith("_"))
        self.assertEqual(len(missing), 2)
        vocabulary = {"boy": {"english": "boy", "chinese": "男孩"}}
        for bad_sentence in ("I have a ____.", "I see six ____.", "It is windy today, ____."):
            repaired = _build_items("T6_WORD_BANK_FILL", {
                "task_type": "T6_WORD_BANK_FILL", "word_bank": ["boy"], "items": [{
                    "sentence_with_blank": bad_sentence, "answer": "boy",
                    "cn_translation": "我有一个男孩。",
                }]}, 1, vocabulary, set())
            self.assertEqual(repaired[0]["sentence"], "I can see a ____.")
            self.assertEqual(repaired[0]["cn_translation"], "我看见了男孩。")
        with self.assertRaisesRegex(ValueError, "泄露答案"):
            _build_items("T2_READ_AND_MATCH", {"task_type": "T2_READ_AND_MATCH", "items": [{
                "word": "boy", "clue": "It is a boy.", "cn_meaning": "男孩",
            }]}, 1, vocabulary, set())
        bank_words = WORDS[:12]
        all_words = {word.casefold(): {"english": word, "chinese": word} for word in bank_words}
        trimmed = _build_items("T6_WORD_BANK_FILL", {
            "task_type": "T6_WORD_BANK_FILL", "word_bank": bank_words, "items": [{
                "sentence_with_blank": "It is an ____.", "answer": "apple",
                "cn_translation": "这是一个苹果。",
            }]}, 1, all_words, set())
        self.assertLessEqual(len(trimmed[0]["word_bank"]), 4)
        self.assertIn("apple", trimmed[0]["word_bank"])
        small_vocabulary = {"cat": {"english": "cat", "chinese": "猫"},
                            "dog": {"english": "dog", "chinese": "狗"}}
        repaired = _build_items("T6_WORD_BANK_FILL", {
            "task_type": "T6_WORD_BANK_FILL", "word_bank": ["cat"], "items": [{
                "sentence_with_blank": "I can see a ____.", "answer": "dog",
                "cn_translation": "我能看到一只狗。",
            }]}, 1, small_vocabulary, set())
        self.assertIn("dog", repaired[0]["word_bank"])

    def test_web_generates_and_grades_quiz(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_dir = root / "BookScan"
            input_dir.mkdir()
            output = root / "output" / "vocabulary.json"
            ai = FakeQuizAI()
            state = AppState(input_dir, output, output.parent / "scan_manifest.json",
                             ROOT / "web", ai, scale=1, quality=80, retries=0)
            output.parent.mkdir()
            output.write_text(json.dumps({"entries": [
                {"english": word, "chinese": word} for word in WORDS]}), encoding="utf-8")
            server = make_server(state)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                self.assertTrue(json.load(urlopen(base + "/api/quiz/status"))["auto_generate_pending"])
                start = Request(base + "/api/quiz/generate", data=b"", method="POST",
                                headers={"X-SmartVocabQuiz": "1"})
                self.assertEqual(urlopen(start).status, 202)
                self.assertFalse(json.load(urlopen(base + "/api/quiz/status"))["auto_generate_pending"])
                for _ in range(100):
                    status = json.load(urlopen(base + "/api/quiz/status"))
                    if status["state"] != "running":
                        break
                    time.sleep(0.02)
                self.assertEqual(status["state"], "done")
                public = json.load(urlopen(base + "/api/quiz/latest"))
                self.assertEqual(public["question_count"], 15)
                self.assertNotIn('"answer"', json.dumps(public))
                answers = json.load(urlopen(base + f"/api/quiz/answers?id={public['id']}"))
                responses = {str(item["number"]): item["answer"] for item in answers["details"]}
                grade_request = Request(base + "/api/quiz/grade",
                                        data=json.dumps({"quiz_id": public["id"],
                                                         "responses": responses}).encode(),
                                        method="POST", headers={"X-SmartVocabQuiz": "1"})
                self.assertEqual(json.load(urlopen(grade_request))["correct"], 15)
            finally:
                server.shutdown()
                server.server_close()

    def test_generates_fifteen_questions_and_grades_without_public_answers(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            vocabulary = root / "vocabulary.json"
            vocabulary.write_text(json.dumps({"entries": [
                {"english": word, "chinese": word} for word in WORDS]}), encoding="utf-8")
            ai = FakeQuizAI()
            quiz = generate_quiz(vocabulary, ROOT / "docs" / "KIDS_VOCAB_QUIZ_MAKER.md",
                                 root / "quizzes", ai)
            self.assertEqual(ai.checks, 1)
            self.assertEqual(ai.calls, ["T1_ODD_ONE_OUT"] + ["T2_READ_AND_MATCH"] * 3 + [
                "T4_MISSING_LETTERS", "T5_UNSCRAMBLE_WORDS",
                "T6_WORD_BANK_FILL", "T11_DIALOGUE_REPLY"])
            self.assertEqual([len(section["questions"]) for section in quiz["sections"]], [5, 5, 5])
            self.assertLessEqual(len(quiz["sections"][2]["questions"][0]["word_bank"]), 8)
            self.assertTrue((root / "quizzes" / f"{quiz['id']}.json").is_file())
            self.assertIn("red", _previous_answer_words(root / "quizzes"))
            public = public_quiz(quiz)
            self.assertNotIn('"answer"', json.dumps(public))
            questions = [question for section in quiz["sections"] for question in section["questions"]]
            responses = {str(question["number"]): question["answer"] for question in questions}
            self.assertEqual(grade_quiz(quiz, responses)["correct"], 15)
            responses["1"] = "wrong"
            self.assertEqual(grade_quiz(quiz, responses)["correct"], 14)
            self.assertNotEqual(questions[8]["scrambled"].replace(" ", "").casefold(),
                                questions[8]["answer"].casefold())

    def test_ignores_model_answers_outside_fresh_candidates(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            vocabulary = root / "vocabulary.json"
            vocabulary.write_text(json.dumps({"entries": [
                {"english": word, "chinese": word} for word in WORDS]}), encoding="utf-8")
            quiz_dir = root / "quizzes"
            quiz_dir.mkdir()
            (quiz_dir / "20260101T000000-aaaaaaaa.json").write_text(json.dumps({
                "sections": [{"questions": [{"answer": "tiger"}, {"answer": "rabbit"}]}]
            }), encoding="utf-8")
            quiz = generate_quiz(vocabulary, ROOT / "docs" / "KIDS_VOCAB_QUIZ_MAKER.md",
                                 quiz_dir, FakeQuizAI())
            feature_questions = [item for section in quiz["sections"] for item in section["questions"]
                                 if item["type"] == "T2_READ_AND_MATCH"]
            self.assertEqual({item["answer"] for item in feature_questions}, {"panda", "fish"})


if __name__ == "__main__":
    unittest.main()
