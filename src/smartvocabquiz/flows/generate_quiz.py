"""使用本地词库和单题型 AI 调用生成 15 题组合练习。"""

from __future__ import annotations

import json
import random
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from logging_config import get_logger


logger = get_logger(__name__)
TASKS = (("T1_ODD_ONE_OUT", 1), ("T2_READ_AND_MATCH", 2),
         ("T10_CN_EN_MATCH", 2), ("T4_MISSING_LETTERS", 3),
         ("T5_UNSCRAMBLE_WORDS", 2), ("T6_WORD_BANK_FILL", 3),
         ("T11_DIALOGUE_REPLY", 2))
SIMPLE_WORD = re.compile(r"[A-Za-z]{3,}")
DIALOGUE_NOUNS = set("cat dog bird fish rabbit tiger bear lion monkey panda mouse wolf pet animal apple book chair desk pencil schoolbag window door tree house room table bed sofa toy robot balloon clock photo picture mask zoo classroom school family".split())
FEATURE_CLUES = {
    "tiger": "It is big and strong.",
    "rabbit": "It is small and cute.",
    "panda": "It is black and white.",
    "fish": "It can swim.",
}
SIGHT_WORDS = set("I am is are a an the this that my your it he she can see like have big small long short on in under".casefold().split())
SIGHT_WORDS.update("red orange yellow green blue purple pink black white brown one two three four five six seven eight nine ten".split())
TASK_RULES = {
    "T1_ODD_ONE_OUT": "每题 options 恰好 4 个不同的词，其中 3 个同类，1 个不同类；answer 必须是那个不同类的词。",
    "T2_READ_AND_MATCH": "每题给一个词和只用允许词汇写的简短英文特征描述。clue 不能出现答案词本身；两道题应有不同且清楚的特征。",
    "T4_MISSING_LETTERS": "每题选择至少 4 个字母的单词；只遮盖中间连续 2 个字母，保留首尾字母，missing_part 是被遮盖的 2 个字母。",
    "T5_UNSCRAMBLE_WORDS": "每题选择至少 4 个字母的单词；只给正确单词，程序负责打乱字母。",
    "T6_WORD_BANK_FILL": "只选择提供的具体名词作为答案。word_bank 包含每题不同的答案和最多 3 个干扰词，不要复制整个 vocab_list。每题一个 ____ 空格；句子只用允许的英文词汇。优先使用 I can see a/an ____、I like the ____、This is a/an ____ 这些简单句式。中文翻译要准确指向答案。",
    "T8_PICTURE_WORD_CHOICE": "每题 target_word 是容易画出的具体物体或动物，options 为它和 3 个同类但不同的词；image_id 唯一，不要生成图片提示词。",
    "T9_IMAGE_PROMPT_GEN": "只为指定的一个 T8 题目生成黑白线稿提示词。positive_prompt 只描绘目标物，negative_prompt 排除其他选项对应的物体和文字。",
    "T10_CN_EN_MATCH": "每题只输出正确的英文和中文释义配对，中文释义要各不相同；程序打乱英文列。",
    "T11_DIALOGUE_REPLY": "只从提供的具体名词中选择目标词。中文场景先自然地说明已经看见的目标物，再写朋友的提问；目标词中文释义必须出现在陈述中，不要写‘这是什么书’或‘看到什么熊’这类把答案塞进问句的句子。英文问句优先用 What is this? 或 What can you see?，不要用 Where。三个回答中只有正确回答包含目标词，另外两个不能包含目标词。",
}


def load_skill_prompt(path: Path, task_type: str) -> str:
    """仅把对应题型及通用约束交给模型，确保每次只做一个原子任务。"""
    text = path.read_text(encoding="utf-8")
    marker = "# Task Types & JSON Schemas"
    if marker not in text:
        raise ValueError("出题 skill 缺少任务定义")
    preface, tasks = text.split(marker, 1)
    decoder = json.JSONDecoder()
    position = 0
    while position < len(tasks):
        position = tasks.find("{", position)
        if position < 0:
            break
        try:
            schema, consumed = decoder.raw_decode(tasks[position:])
        except json.JSONDecodeError:
            position += 1
            continue
        position += consumed
        if isinstance(schema, dict) and schema.get("task_type") == task_type:
            fields = ", ".join(schema)
            item_fields = ", ".join((schema.get("items") or schema.get("prompts") or [{}])[0])
            return (f"{preface.strip()}\n\n只执行 {task_type}。{TASK_RULES[task_type]}\n"
                    f"输出严格合法的单个 JSON 对象。顶层字段：{fields}。"
                    f"题目或提示词条目字段：{item_fields}。"
                    f"instruction_cn 固定为：{schema.get('instruction_cn', '')}。"
                    f"instruction_en 固定为：{schema.get('instruction_en', '')}。"
                    "所有英文词必须从本次 vocab_list 中选取；不要照抄任何示例词。")
    raise ValueError(f"出题 skill 缺少 {task_type} 定义")


def _word(value: Any, vocabulary: dict[str, dict[str, str]]) -> str:
    if not isinstance(value, str) or value.casefold() not in vocabulary:
        raise ValueError(f"题目使用了词库外的单词：{value!r}")
    return vocabulary[value.casefold()]["english"]


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"题目缺少 {name}")
    return value.strip()


def _unique_answer(answer: str, used: set[str]) -> None:
    if answer.casefold() in used:
        raise ValueError(f"试卷答案重复：{answer}")
    used.add(answer.casefold())


def _validate_sentence(sentence: str, vocabulary: dict[str, dict[str, str]]) -> None:
    for word in re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", sentence):
        if word.casefold() not in SIGHT_WORDS and word.casefold() not in vocabulary:
            raise ValueError(f"英文句子超出低年级词汇范围：{word}")


def _mask_word(word: str, masked: Any, missing: Any) -> tuple[str, str]:
    """保留有效的 AI 遮字母结果，其他情况由程序确定可还原的遮盖。"""
    if isinstance(masked, str) and isinstance(missing, str):
        letters = iter(missing)
        try:
            restored = "".join(next(letters) if char == "_" else char for char in masked)
        except StopIteration:
            restored = ""
        if (re.fullmatch(r"[A-Za-z_]+", masked) and masked.count("_") == 2
                and "__" in masked and not masked.startswith("_") and not masked.endswith("_")
                and len(masked) == len(word) and len(missing) == 2
                and restored.casefold() == word.casefold()):
            return masked, missing
    start = random.randint(1, len(word) - 3)
    return word[:start] + "__" + word[start + 2:], word[start:start + 2]


def _fallback_fill(answer: str, chinese: str, index: int) -> tuple[str, str]:
    """模型句子越界时使用可判分的低年级句式。"""
    meaning = re.split(r"[，,；;、（(]", chinese, 1)[0].strip()
    article = "an" if answer[0].casefold() in "aeiou" else "a"
    templates = (
        (f"I can see {article} ____.", f"我看见了{meaning}。"),
        ("I like the ____.", f"我喜欢{meaning}。"),
        (f"This is {article} ____.", f"这是{meaning}。"),
    )
    return templates[index % len(templates)]


def _build_items(task_type: str, data: dict[str, Any], count: int,
                 vocabulary: dict[str, dict[str, str]], used: set[str]) -> list[dict[str, Any]]:
    if data.get("task_type") != task_type or not isinstance(data.get("items"), list):
        raise ValueError(f"{task_type} 的 JSON 结构不正确")
    if len(data["items"]) != count:
        raise ValueError(f"{task_type} 应生成 {count} 题，实际 {len(data['items'])} 题")
    seen = set(used)
    result = []
    bank: list[str] = []
    if task_type == "T6_WORD_BANK_FILL":
        raw_bank = data.get("word_bank")
        if isinstance(raw_bank, list):
            seen_bank: set[str] = set()
            for value in raw_bank:
                if isinstance(value, str) and value.casefold() in vocabulary and value.casefold() not in seen_bank:
                    bank.append(_word(value, vocabulary))
                    seen_bank.add(value.casefold())
        random.shuffle(bank)
    for item in data["items"]:
        if not isinstance(item, dict):
            raise ValueError("题目条目不是对象")
        if task_type == "T1_ODD_ONE_OUT":
            options = item.get("options")
            if not isinstance(options, list) or len(options) != 4:
                raise ValueError("找不同类必须有 4 个选项")
            options = [_word(word, vocabulary) for word in options]
            answer = _word(item.get("answer"), vocabulary)
            if len({word.casefold() for word in options}) != 4 or answer not in options:
                raise ValueError("找不同类的选项或答案无效")
            _unique_answer(answer, seen)
            random.shuffle(options)
            result.append({"type": task_type, "options": options, "answer": answer,
                           "category_hint": _text(item.get("category_hint"), "类别提示")})
        elif task_type == "T2_READ_AND_MATCH":
            answer = _word(item.get("word"), vocabulary)
            _unique_answer(answer, seen)
            clue = _text(item.get("clue"), "特征描述")
            try:
                _validate_sentence(clue, vocabulary)
                if answer.casefold() in {word.casefold() for word in re.findall(r"[A-Za-z]+", clue)}:
                    raise ValueError(f"特征描述直接泄露答案：{answer}")
            except ValueError:
                clue = FEATURE_CLUES.get(answer.casefold(), "")
                if not clue:
                    raise
                _validate_sentence(clue, vocabulary)
            result.append({"type": task_type, "clue": clue,
                           "answer": answer, "cn_meaning": vocabulary[answer.casefold()]["chinese"]})
        elif task_type == "T4_MISSING_LETTERS":
            word = _word(item.get("word"), vocabulary)
            if not SIMPLE_WORD.fullmatch(word) or len(word) < 4:
                raise ValueError("补全字母只支持至少 4 个字母的单词")
            masked, missing = _mask_word(word, item.get("masked_word"), item.get("missing_part"))
            _unique_answer(word, seen)
            result.append({"type": task_type, "masked_word": masked, "answer": missing,
                           "word": word, "cn_meaning": vocabulary[word.casefold()]["chinese"]})
        elif task_type == "T5_UNSCRAMBLE_WORDS":
            word = _word(item.get("word"), vocabulary)
            if not SIMPLE_WORD.fullmatch(word) or len(word) < 4:
                raise ValueError("字母重组只支持至少 4 个字母的单词")
            _unique_answer(word, seen)
            letters = list(word)
            for _ in range(12):
                random.shuffle(letters)
                if "".join(letters).casefold() != word.casefold():
                    break
            else:
                raise ValueError(f"无法打乱字母：{word}")
            result.append({"type": task_type, "scrambled": " ".join(letters), "answer": word,
                           "cn_meaning": vocabulary[word.casefold()]["chinese"],
                           "phonics_hint": item.get("phonics_hint", "") if isinstance(item.get("phonics_hint", ""), str) else ""})
        elif task_type == "T6_WORD_BANK_FILL":
            answer = _word(item.get("answer"), vocabulary)
            if answer not in bank:
                bank.append(answer)
            _unique_answer(answer, seen)
            try:
                sentence = _text(item.get("sentence_with_blank"), "填空句子")
                translation = _text(item.get("cn_translation"), "中文翻译")
                if sentence.count("____") != 1:
                    raise ValueError("填空句子必须恰有一个 ____")
                _validate_sentence(sentence, vocabulary)
                if (re.search(r"\bhave (?:a|an) ____\b", sentence, re.IGNORECASE)
                        and answer.casefold() in {"boy", "girl", "man", "woman", "baby", "teacher"}):
                    raise ValueError("把人当成物品")
                if (re.search(r"\b(?:two|three|four|five|six|seven|eight|nine|ten) ____\b", sentence,
                              re.IGNORECASE) and answer.casefold() != "fish"):
                    raise ValueError("数量词后缺少复数形式")
            except ValueError:
                sentence, translation = _fallback_fill(answer, vocabulary[answer.casefold()]["chinese"],
                                                       len(result))
            result.append({"type": task_type, "sentence": sentence, "answer": answer,
                           "cn_translation": translation,
                           "word_bank": bank})
        elif task_type == "T8_PICTURE_WORD_CHOICE":
            target = _word(item.get("target_word"), vocabulary)
            options = item.get("options")
            if not isinstance(options, list) or len(options) != 4:
                raise ValueError("看图选词必须有 4 个选项")
            options = [_word(word, vocabulary) for word in options]
            answer = _word(item.get("answer"), vocabulary)
            if (len({word.casefold() for word in options}) != 4
                    or answer.casefold() != target.casefold() or target not in options):
                raise ValueError("看图选词的目标与选项不匹配")
            _unique_answer(target, seen)
            random.shuffle(options)
            result.append({"type": task_type, "target_word": target, "options": options,
                           "answer": target, "category_hint": _text(item.get("category_hint"), "类别")})
        elif task_type == "T10_CN_EN_MATCH":
            answer = _word(item.get("word"), vocabulary)
            _unique_answer(answer, seen)
            result.append({"type": task_type, "cn_meaning": vocabulary[answer.casefold()]["chinese"],
                           "answer": answer})
        elif task_type == "T11_DIALOGUE_REPLY":
            target = _word(item.get("target_word"), vocabulary)
            _unique_answer(target, seen)
            scene = _text(item.get("scene_cn"), "中文场景")
            meanings = [part.strip() for part in re.split(r"[，,；;、]", vocabulary[target.casefold()]["chinese"])
                        if part.strip()]
            if not any(meaning in scene for meaning in meanings):
                raise ValueError(f"对话场景没有明确提示目标词中文释义：{target}")
            if any(re.search(rf"(?:什么|哪[个只本条])[^。！？!?]{{0,5}}{re.escape(meaning)}", scene)
                   for meaning in meanings):
                raise ValueError(f"对话场景把目标词塞进了问句：{scene}")
            question = _text(item.get("question"), "英文问话")
            _validate_sentence(question, vocabulary)
            options = item.get("options")
            if not isinstance(options, list) or len(options) != 3:
                raise ValueError("对话选答必须有 3 个回答")
            options = [_text(option, "回答") for option in options]
            if len({option.casefold() for option in options}) != 3:
                raise ValueError("对话选答的回答有重复")
            for option in options:
                _validate_sentence(option, vocabulary)
            answer = _text(item.get("answer"), "正确回答")
            if answer not in options:
                raise ValueError("对话选答的答案不在选项中")
            mentions = [target.casefold() in {word.casefold() for word in re.findall(r"[A-Za-z]+", option)}
                        for option in options]
            if sum(mentions) != 1 or not mentions[options.index(answer)]:
                raise ValueError("对话选答的目标词不能唯一定位答案")
            random.shuffle(options)
            result.append({"type": task_type, "scene_cn": scene, "question": question,
                           "options": options, "answer": answer, "target_word": target})
    if task_type == "T6_WORD_BANK_FILL":
        answers = {item["answer"].casefold() for item in result}
        chosen = [word for word in bank if word.casefold() in answers]
        distractors = [word for word in bank if word.casefold() not in answers]
        random.shuffle(distractors)
        chosen.extend(distractors[:3])
        random.shuffle(chosen)
        for item in result:
            item["word_bank"] = chosen
    if task_type == "T10_CN_EN_MATCH":
        meanings = [item["cn_meaning"] for item in result]
        if len(set(meanings)) != len(meanings):
            raise ValueError("中英配对的中文释义重复")
        choices = [item["answer"] for item in result]
        random.shuffle(choices)
        for item in result:
            item["options"] = choices
    if task_type == "T2_READ_AND_MATCH" and len({item["clue"].casefold() for item in result}) != count:
        for item in result:
            item["clue"] = FEATURE_CLUES[item["answer"].casefold()]
    used.update(seen)
    return result


def _match_data(vocab_list: list[dict[str, str]], count: int, used: set[str]) -> dict[str, Any]:
    """中英配对直接从词库选取原始配对，避免模型改写中文释义。"""
    candidates = [item for item in vocab_list if item["english"].casefold() not in used
                  and len(item["english"]) >= 3 and len(item["chinese"]) <= 10]
    random.shuffle(candidates)
    selected: list[dict[str, str]] = []
    meanings: set[str] = set()
    for item in candidates:
        meaning = item["chinese"].strip()
        if meaning and meaning not in meanings:
            selected.append({"word": item["english"], "cn_meaning": meaning})
            meanings.add(meaning)
        if len(selected) == count:
            break
    if len(selected) < count:
        raise ValueError("词库中的不同中英配对不足")
    return {"task_type": "T10_CN_EN_MATCH", "items": selected}


def _feature_data(vocab_list: list[dict[str, str]], count: int) -> dict[str, Any]:
    """模型反复忽略候选词时，使用已核对的特征描述保留题目变化。"""
    candidates = [item for item in vocab_list if item["english"].casefold() in FEATURE_CLUES]
    if len(candidates) < count:
        raise ValueError("可用于特征连线的单词不足")
    return {"task_type": "T2_READ_AND_MATCH", "items": [
        {"word": item["english"], "clue": FEATURE_CLUES[item["english"].casefold()],
         "cn_meaning": item["chinese"]}
        for item in random.sample(candidates, count)]}


def _previous_answer_words(quiz_dir: Path) -> set[str]:
    """读取上一份试卷的目标词，用于下一份组卷时优先避开。"""
    files = sorted(quiz_dir.glob("*.json")) if quiz_dir.is_dir() else []
    if not files:
        return set()
    try:
        previous = json.loads(files[-1].read_text(encoding="utf-8"))
        questions = [item for section in previous["sections"] for item in section["questions"]]
    except (OSError, ValueError, KeyError, TypeError):
        logger.warning("无法读取上一份试卷的答案，继续生成新卷")
        return set()
    answers = set()
    for item in questions:
        if not isinstance(item, dict):
            continue
        word = item.get("target_word") or item.get("word") or item.get("answer")
        if isinstance(word, str) and SIMPLE_WORD.fullmatch(word):
            answers.add(word.casefold())
    return answers


def generate_quiz(vocabulary_file: Path, skill_file: Path, quiz_dir: Path, ai: Any,
                  progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    ai.check_available()
    payload = json.loads(vocabulary_file.read_text(encoding="utf-8"))
    entries = payload.get("entries", [])
    words = {item["english"].casefold(): {"english": item["english"], "chinese": item["chinese"]}
             for item in entries if isinstance(item, dict)
             and isinstance(item.get("english"), str) and isinstance(item.get("chinese"), str)
             and SIMPLE_WORD.fullmatch(item["english"])}
    if len(words) < 30:
        raise ValueError("可出题的单词不足 30 个，请先扩充词库")
    vocab_list = list(words.values())
    random.shuffle(vocab_list)
    previous_answers = _previous_answer_words(quiz_dir)
    used: set[str] = set()
    all_items: list[dict[str, Any]] = []
    for task_type, count in TASKS:
        if progress:
            progress(f"正在生成 {task_type}：{count} 题…")
        prompt = load_skill_prompt(skill_file, task_type)
        task_vocabulary = [item for item in vocab_list if item["english"].casefold() not in used
                           and (task_type not in ("T4_MISSING_LETTERS", "T5_UNSCRAMBLE_WORDS")
                                or len(item["english"]) >= 4)]
        if task_type in ("T6_WORD_BANK_FILL", "T11_DIALOGUE_REPLY"):
            task_vocabulary = [item for item in task_vocabulary
                               if item["english"].casefold() in DIALOGUE_NOUNS]
            if len(task_vocabulary) < 8:
                raise ValueError("可用于语境题的具体名词不足 8 个")
        if task_type == "T2_READ_AND_MATCH":
            task_vocabulary = [item for item in task_vocabulary
                               if item["english"].casefold() in FEATURE_CLUES
                               and all(word.casefold() in SIGHT_WORDS or word.casefold() in words
                                       for word in re.findall(r"[A-Za-z]+",
                                                              FEATURE_CLUES[item["english"].casefold()]))]
            if len(task_vocabulary) < count:
                raise ValueError("可用于特征连线的单词不足")
        fresh_vocabulary = [item for item in task_vocabulary
                            if item["english"].casefold() not in previous_answers]
        required_fresh = (8 if task_type == "T11_DIALOGUE_REPLY"
                          else 4 if task_type in ("T1_ODD_ONE_OUT", "T8_PICTURE_WORD_CHOICE")
                          else count)
        avoid_previous = len(fresh_vocabulary) >= required_fresh
        if avoid_previous:
            task_vocabulary = fresh_vocabulary
        allowed_answers = {item["english"].casefold() for item in task_vocabulary}
        feedback = ""
        for attempt in range(3):
            data = (_match_data(task_vocabulary, count, used) if task_type == "T10_CN_EN_MATCH"
                    else ai.generate_task(prompt, task_type, task_vocabulary, count,
                                          sorted(used | (previous_answers if avoid_previous else set())), feedback))
            try:
                trial_used = set(used)
                items = _build_items(task_type, data, count, words, trial_used)
                outside_candidates = [item.get("target_word") or item.get("word") or item.get("answer")
                                      for item in items]
                outside_candidates = [word for word in outside_candidates if isinstance(word, str)
                                      and word.casefold() not in allowed_answers]
                if outside_candidates:
                    raise ValueError(f"答案不在本次候选词里：{', '.join(outside_candidates)}")
                if avoid_previous:
                    repeated = [item.get("target_word") or item.get("word") or item.get("answer")
                                for item in items]
                    repeated = [word for word in repeated if isinstance(word, str)
                                and word.casefold() in previous_answers]
                    if repeated:
                        raise ValueError(f"与上一份试卷重复：{', '.join(repeated)}")
                if task_type == "T2_READ_AND_MATCH":
                    choices = [item["answer"] for item in items]
                    random.shuffle(choices)
                    for item in items:
                        item["options"] = choices
                used = trial_used
                all_items.extend(items)
                break
            except ValueError as exc:
                feedback = str(exc)
                logger.warning("%s 第 %s 次结果未通过校验：%s", task_type, attempt + 1, exc)
                if attempt == 2:
                    if task_type == "T2_READ_AND_MATCH":
                        fallback_used = set(used)
                        fallback_items = _build_items(task_type, _feature_data(task_vocabulary, count),
                                                      count, words, fallback_used)
                        choices = [item["answer"] for item in fallback_items]
                        random.shuffle(choices)
                        for item in fallback_items:
                            item["options"] = choices
                        used = fallback_used
                        all_items.extend(fallback_items)
                        logger.info("T2 使用已核对的特征描述完成出题")
                        break
                    raise ValueError(f"{task_type} 连续 3 次未通过题目校验：{exc}") from exc
    for number, item in enumerate(all_items, 1):
        item["number"] = number
    now = datetime.now(timezone.utc)
    quiz_id = f"{now.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"
    quiz = {
        "schema_version": 1, "id": quiz_id, "generated_at": now.isoformat(),
        "duration_minutes": "15–20", "question_count": 15,
        "sections": [
            {"title": "热身认读", "minutes": 5, "questions": all_items[:5]},
            {"title": "拼写巩固", "minutes": 7, "questions": all_items[5:10]},
            {"title": "语境读写", "minutes": 5, "questions": all_items[10:]},
        ],
    }
    quiz_dir.mkdir(parents=True, exist_ok=True)
    target = quiz_dir / f"{quiz_id}.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(quiz, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)
    if progress:
        progress("15 道试题已生成")
    return quiz


def public_quiz(quiz: dict[str, Any]) -> dict[str, Any]:
    """网页初次加载时不附带答案。"""
    clean = json.loads(json.dumps(quiz, ensure_ascii=False))
    for section in clean["sections"]:
        for question in section["questions"]:
            for key in ("answer", "category_hint", "word"):
                question.pop(key, None)
    return clean


def regenerate_question(vocabulary_file: Path, skill_file: Path, quiz: dict[str, Any],
                        number: int, ai: Any) -> dict[str, Any]:
    """只重出一个题位，保留其余题目和已审核内容。"""
    questions = [item for section in quiz["sections"] for item in section["questions"]]
    old = next((item for item in questions if item["number"] == number), None)
    if old is None:
        raise ValueError("题号不存在")
    task_type = old["type"]
    entries = json.loads(vocabulary_file.read_text(encoding="utf-8")).get("entries", [])
    words = {item["english"].casefold(): {"english": item["english"], "chinese": item["chinese"]}
             for item in entries if isinstance(item, dict)
             and isinstance(item.get("english"), str) and isinstance(item.get("chinese"), str)
             and SIMPLE_WORD.fullmatch(item["english"])}
    used = {str(item.get("target_word") or item.get("word") or item.get("answer")).casefold()
            for item in questions}
    candidates = [item for item in words.values() if item["english"].casefold() not in used]
    if task_type in ("T4_MISSING_LETTERS", "T5_UNSCRAMBLE_WORDS"):
        candidates = [item for item in candidates if len(item["english"]) >= 4]
    if task_type in ("T6_WORD_BANK_FILL", "T11_DIALOGUE_REPLY"):
        candidates = [item for item in candidates if item["english"].casefold() in DIALOGUE_NOUNS]
    if task_type == "T2_READ_AND_MATCH":
        candidates = [item for item in candidates if item["english"].casefold() in FEATURE_CLUES
                      and all(word.casefold() in SIGHT_WORDS or word.casefold() in words
                              for word in re.findall(r"[A-Za-z]+",
                                                     FEATURE_CLUES[item["english"].casefold()]))]
    minimum = 8 if task_type == "T11_DIALOGUE_REPLY" else 4 if task_type == "T1_ODD_ONE_OUT" else 1
    if len(candidates) < minimum:
        raise ValueError("没有足够的新词可重出这道题，请先扩充词库")
    random.shuffle(candidates)
    allowed = {item["english"].casefold() for item in candidates}
    prompt = load_skill_prompt(skill_file, task_type)
    ai.check_available()
    feedback = ""
    for attempt in range(3):
        data = (_match_data(candidates, 1, used) if task_type == "T10_CN_EN_MATCH"
                else ai.generate_task(prompt, task_type, candidates, 1, sorted(used), feedback))
        try:
            item = _build_items(task_type, data, 1, words, set(used))[0]
            target = item.get("target_word") or item.get("word") or item.get("answer")
            if target.casefold() not in allowed:
                raise ValueError(f"答案不在本次候选词里：{target}")
            if task_type in ("T2_READ_AND_MATCH", "T10_CN_EN_MATCH"):
                distractors = [word for word in old.get("options", []) if word != item["answer"]]
                if not distractors:
                    distractors = [word["english"] for word in words.values()
                                   if word["english"] != item["answer"]]
                item["options"] = [item["answer"], random.choice(distractors)]
                random.shuffle(item["options"])
            item["number"] = number
            return item
        except ValueError as exc:
            feedback = str(exc)
            logger.warning("重出第 %s 题第 %s 次校验未通过：%s", number, attempt + 1, exc)
    if task_type == "T2_READ_AND_MATCH":
        item = _build_items(task_type, _feature_data(candidates, 1), 1, words, set(used))[0]
        distractors = [word for word in old.get("options", []) if word != item["answer"]]
        item["options"] = [item["answer"], random.choice(distractors or list(used))]
        random.shuffle(item["options"])
        item["number"] = number
        return item
    raise ValueError(f"第 {number} 题连续 3 次未通过校验：{feedback}")


def grade_quiz(quiz: dict[str, Any], responses: dict[str, Any]) -> dict[str, Any]:
    details = []
    for section in quiz["sections"]:
        for question in section["questions"]:
            number = str(question["number"])
            given = responses.get(number, "")
            if not isinstance(given, str):
                given = ""
            answer = question["answer"]
            correct = given.strip().casefold() == answer.casefold()
            details.append({"number": question["number"], "given": given.strip(),
                            "answer": answer, "correct": correct})
    return {"total": len(details), "correct": sum(item["correct"] for item in details),
            "details": details}
