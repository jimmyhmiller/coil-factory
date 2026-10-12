"""Generated tasks for the free-model smartness probe, and their grading.

Every probe gets fresh tasks from a random seed, so a model cannot have seen
them. The answer key stays in the registry's state; only the task files enter
the model's workspace. Grading compares answers, never runs model-written code.

The three tasks mirror what a paper run needs:
  archive    read a ~45k-token generated paper archive and combine facts from
             far-apart entries (the size at which Big Pickle stalled);
  loop       say what a program prints when running it would take far too
             long, i.e. see the affine recurrence and compute it fast;
  procedure  implement an unfamiliar algorithm from a prose description.
"""
import random

QUESTIONS = ("cited", "author", "queries", "loop", "procedure-1", "procedure-2", "procedure-3")

CATEGORIES = ("databases", "compilers", "type systems", "garbage collection", "concurrency", "program analysis")
FIRST = ("Ada", "Bram", "Cora", "Dmitri", "Elif", "Farid", "Greta", "Hiro", "Ines", "Jonas", "Kalani", "Lena",
         "Mateo", "Nadia", "Oskar", "Priya", "Quinn", "Rosa", "Soren", "Tamar", "Ugo", "Vera", "Wen", "Yusuf")
LAST = ("Abernathy", "Brandt", "Castellano", "Dvorak", "Eriksen", "Fontaine", "Gallo", "Halvorsen", "Ibarra",
        "Jovanovic", "Kowalczyk", "Lindqvist", "Moreau", "Nakamura", "Okafor", "Pereira", "Quist", "Rasku")
ADJECTIVES = ("incremental", "lazy", "speculative", "persistent", "linear", "partial", "hierarchical", "adaptive",
              "monotone", "staged", "reversible", "versioned", "probabilistic", "concurrent", "dependent")
NOUNS = ("evaluation", "indexing", "unification", "allocation", "scheduling", "snapshots", "regions", "effects",
         "joins", "closures", "barriers", "logs", "continuations", "views", "tries", "traces", "queries")
VENUES = ("POPL", "PLDI", "SIGMOD", "VLDB", "OOPSLA", "ICFP", "SOSP", "CGO", "ECOOP", "ISMM")
FILLER = ("the", "a", "we", "show", "that", "this", "approach", "reduces", "cost", "of", "maintaining", "state",
          "under", "updates", "while", "preserving", "semantics", "our", "prototype", "measures", "overhead",
          "across", "workloads", "and", "compares", "against", "prior", "designs", "using", "simple", "model",
          "results", "suggest", "practical", "benefits", "for", "systems", "with", "large", "inputs")

PROCEDURE_TEXT = """Run the following procedure on a list of whole numbers. It has two
parameters, K = {k} and M = {m}.

1. Call the list the line. Set a counter t to 0. Positions in the line are
   counted from 0.
2. While the line holds more than one number, do one step:
   a. Increase t by 1.
   b. Let i be the remainder of t divided by the current length of the line.
      Remove the number at position i from the line and call it v.
   c. If v is divisible by K, replace the number now at position
      (i mod the new length of the line) with (that number + v) mod M.
   d. Otherwise, if v is odd, replace the last number in the line with
      (that number + v * v) mod M.
   e. Otherwise do nothing more in this step.
3. When one number remains, that number is the result.
"""

LOOP_TEXT = """M = {m}
x, y, z = {x}, {y}, {z}
for step in range({n}):
    x, y, z = y, z, ({a} * x + {b} * y + {c} * z + {d}) % M
print(x)
"""


def name(rng):
    return rng.choice(FIRST) + " " + rng.choice(LAST)


def archive(rng):
    """The archive and its three answers: most-cited paper, top author, needle."""
    authors = sorted({name(rng) for _ in range(60)})
    count = 360
    papers = []
    years = sorted(rng.randint(1962, 2021) for _ in range(count))
    for index, year in enumerate(years):
        cites = sorted(rng.sample(range(index), min(index, rng.randint(0, 4))))
        papers.append({"id": f"P-{index + 1:04d}", "year": year, "venue": rng.choice(VENUES),
                       "category": rng.choice(CATEGORIES), "authors": rng.sample(authors, rng.randint(1, 3)),
                       "title": f"{rng.choice(ADJECTIVES).title()} {rng.choice(NOUNS)} of {rng.choice(NOUNS)}",
                       "cites": cites, "queries": None})
    # Titles must be unique so a title names exactly one paper.
    seen = set()
    for p in papers:
        while p["title"] in seen:
            p["title"] = f"{rng.choice(ADJECTIVES).title()} {rng.choice(NOUNS)} of {rng.choice(NOUNS)}"
        seen.add(p["title"])
    # Most-cited: make the maximum unique by adding citations from later papers.
    while True:
        cited = [0] * count
        for p in papers:
            for c in p["cites"]:
                cited[c] += 1
        top = max(cited)
        tied = [i for i, n in enumerate(cited) if n == top]
        if len(tied) == 1:
            most_cited = tied[0]
            break
        winner = next(i for i in tied if any(i not in p["cites"] for p in papers[i + 1:]))
        citer = next(p for p in papers[winner + 1:] if winner not in p["cites"])
        citer["cites"] = sorted(citer["cites"] + [winner])
    # Several papers report a query count; the question names one by title.
    reporters = rng.sample(range(count), 12)
    for i in reporters:
        papers[i]["queries"] = rng.randint(1000, 99999)
    needle = papers[reporters[rng.randrange(12)]]
    # Top author: a category and year bound with a unique leader of at least 3 papers.
    while True:
        category, before = rng.choice(CATEGORIES), rng.randint(1985, 2015)
        tally = {}
        for p in papers:
            if p["category"] == category and p["year"] < before:
                for a in p["authors"]:
                    tally[a] = tally.get(a, 0) + 1
        best = max(tally.values(), default=0)
        top_authors = [a for a, n in tally.items() if n == best]
        if best >= 3 and len(top_authors) == 1:
            break
    lines = ["# Paper archive", ""]
    for p in papers:
        words = " ".join(rng.choice(FILLER) for _ in range(rng.randint(45, 70)))
        abstract = words[0].upper() + words[1:] + "."
        if p["queries"] is not None:
            abstract += f" The evaluation used exactly {p['queries']:,} queries."
        lines += [f"## {p['id']}: {p['title']}", f"Year: {p['year']}  Venue: {p['venue']}  Category: {p['category']}",
                  "Authors: " + ", ".join(p["authors"]),
                  "Cites: " + (", ".join(papers[c]["id"] for c in p["cites"]) or "none"), "", abstract, ""]
    questions = {
        "cited": "Which paper in archive.md is cited by the largest number of other papers in the archive? Answer with its id, like P-0001.",
        "author": f"Among papers in archive.md in the category \"{category}\" published strictly before {before}, which author wrote the most? Answer with the author's full name.",
        "queries": f"How many queries did the paper titled \"{needle['title']}\" in archive.md use in its evaluation? Answer with a whole number.",
    }
    answers = {"cited": papers[most_cited]["id"], "author": top_authors[0], "queries": needle["queries"]}
    return "\n".join(lines), questions, answers


def affine_power(params, n):
    """x after n steps of the loop, by fast exponentiation of its 4x4 matrix."""
    m, x, y, z, a, b, c, d = params
    def mul(p, q):
        return [[sum(p[i][k] * q[k][j] for k in range(4)) % m for j in range(4)] for i in range(4)]
    result = [[int(i == j) for j in range(4)] for i in range(4)]
    base = [[0, 1, 0, 0], [0, 0, 1, 0], [a % m, b % m, c % m, d % m], [0, 0, 0, 1]]
    while n:
        if n & 1:
            result = mul(base, result)
        base = mul(base, base)
        n >>= 1
    state = [x, y, z, 1]
    return sum(result[0][k] * state[k] for k in range(4)) % m


def loop(rng):
    m = rng.choice((1000003, 998244353, 1000000007, 2147483647))
    params = (m, rng.randrange(m), rng.randrange(m), rng.randrange(m),
              rng.randrange(2, m), rng.randrange(2, m), rng.randrange(2, m), rng.randrange(1, m))
    n = 10 ** 15 + rng.randrange(10 ** 6)
    text = LOOP_TEXT.format(m=m, x=params[1], y=params[2], z=params[3], a=params[4], b=params[5],
                            c=params[6], d=params[7], n=n)
    question = "What does loop.py print? Running it would take far too long; work out what it computes. Answer with a whole number."
    return text, question, affine_power(params, n)


def procedure(values, k, m):
    line, t = list(values), 0
    while len(line) > 1:
        t += 1
        i = t % len(line)
        v = line.pop(i)
        if v % k == 0:
            j = i % len(line)
            line[j] = (line[j] + v) % m
        elif v % 2 == 1:
            line[-1] = (line[-1] + v * v) % m
    return line[0]


def procedures(rng):
    k, m = rng.randint(3, 5), rng.choice((101, 103, 107, 109, 113, 127, 131))
    inputs = [[rng.randint(1, 60) for _ in range(rng.randint(8, 12))] for _ in range(3)]
    questions = {f"procedure-{n + 1}": f"What is the result of PROCEDURE.md on the list {values}? Answer with a whole number."
                 for n, values in enumerate(inputs)}
    answers = {f"procedure-{n + 1}": procedure(values, k, m) for n, values in enumerate(inputs)}
    return PROCEDURE_TEXT.format(k=k, m=m), questions, answers


def generate(seed):
    """Files for the workspace, and the key that stays with the registry."""
    rng = random.Random(seed)
    archive_text, questions, key = archive(rng)
    loop_text, loop_question, loop_answer = loop(rng)
    procedure_text, procedure_questions, procedure_answers = procedures(rng)
    questions.update(loop=loop_question, **procedure_questions)
    key.update(loop=loop_answer, **procedure_answers)
    tasks = ["# Tasks", "",
             "Answer every question below. Write ANSWERS.json in this directory: one JSON object whose keys are the",
             "question ids and whose values are your answers (a string for ids and names, a number otherwise).",
             "You may read files and write and run your own programs. Work carefully; each answer is graded exactly.", ""]
    for qid in QUESTIONS:
        tasks.append(f"- `{qid}`: {questions[qid]}")
    files = {"TASKS.md": "\n".join(tasks) + "\n", "archive.md": archive_text, "loop.py": loop_text,
             "PROCEDURE.md": procedure_text}
    return files, key


def normalize(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value) if float(value).is_integer() else value
    if isinstance(value, str):
        text = " ".join(value.split()).casefold()
        digits = text.replace(",", "")
        return int(digits) if digits.isdigit() else text
    return None


def grade(answers, key):
    """Per-question correctness; anything missing or malformed is wrong."""
    if not isinstance(answers, dict):
        return {qid: False for qid in QUESTIONS}
    return {qid: qid in answers and normalize(answers[qid]) is not None and normalize(answers[qid]) == normalize(key[qid])
            for qid in QUESTIONS}
