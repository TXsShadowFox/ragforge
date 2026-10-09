# Evaluation results

Made by `make eval` on 2026-10-09, in 11 minutes.

- **Test set:** 6 documents of a made-up college (2,788 words: campus-services.md, housing-rules.html, it-help.txt, library-guide.md, snack-bar-notice.txt, student-handbook.pdf) and 50 questions: 38 with an answer in the documents, 12 without.
- **Models:** embedder `BAAI/bge-small-en-v1.5`, reranker `Xenova/ms-marco-MiniLM-L-6-v2`, LLM `openai/gpt-oss-20b`, judge `openai/gpt-oss-120b`.

## Search quality

Is a chunk with the answer's evidence among the first k results? (38 questions; MRR = mean of 1/place of the first such chunk, 0 below place 10.)

| Setting | Chunks | hit@1 | hit@3 | hit@5 | MRR@10 |
|---|---:|---:|---:|---:|---:|
| Hybrid search + reranker (the API's setting) | 15 | 97% | 100% | 100% | 0.987 |
| Hybrid search, no reranker | 15 | 87% | 100% | 100% | 0.930 |
| Vector search only + reranker | 15 | 97% | 100% | 100% | 0.987 |
| Keyword search only + reranker | 15 | 97% | 100% | 100% | 0.987 |
| Vector search only, no reranker | 15 | 87% | 95% | 97% | 0.908 |
| Chunks of 300 tokens (hybrid + reranker) | 16 | 92% | 100% | 100% | 0.956 |
| Chunks of 1000 tokens (hybrid + reranker) | 11 | 95% | 97% | 100% | 0.967 |

The embedder reads at most 512 tokens, so a 1000-token chunk is embedded from its first half only (the keyword search and the reranker still read more of it).

**The "I don't know" gate** (no chunk scores at least MIN_RERANK_SCORE = -10, so the LLM is not called): it stopped 4 of 12 questions that are not in the documents, and 0 of 38 that are.

What other thresholds would do, with the reranker's best score per question:

| MIN_RERANK_SCORE | Stops wrongly (the documents have the answer) | Stops rightly (they do not) |
|---:|---:|---:|
| -4 | 8 of 38 | 9 of 12 |
| -5 | 5 of 38 | 8 of 12 |
| -6 | 3 of 38 | 7 of 12 |
| -7 | 2 of 38 | 7 of 12 |
| -8 | 2 of 38 | 7 of 12 |
| -9 | 1 of 38 | 6 of 12 |
| -10 (the setting) | 0 of 38 | 4 of 12 |
| -11 | 0 of 38 | 3 of 12 |

Best scores, lowest first (* = not in the documents): q46* -11.1, q45* -11.1, q48* -11.1, q47* -11.0, q38* -9.9, q25 -9.7, q37* -9.1, q15 -8.7, q44* -8.3, q33 -6.3, q05 -5.9, q26 -5.9, q41* -5.2, q21 -4.9, q34 -4.5, q40* -4.4, q27 -4.1, q36 -4.0, q31 -3.7, q07 -2.2, q19 -2.2, q18 -1.6, q04 -1.5, q43* -1.3, q30 -0.9, q11 0.2, q28 0.3, q16 0.3, q02 0.5, q35 1.1, q22 1.2, q17 1.2, q32 1.4, q08 1.7, q39* 2.8, q09 3.0, q06 3.3, q42* 3.4, q23 3.7, q10 4.1, q12 4.7, q29 4.8, q01 4.8, q03 4.9, q20 5.9, q13 6.5, q24 6.6, q14 6.7, q50 7.8, q49 9.2

## Answer quality

The API's setting (500-token chunks, hybrid search, reranker, top 5 sources), without the answer cache. The judge reads the question, the reference answer, the sources and the answer.

| Measure | Result |
|---|---|
| Faithfulness: every fact is in the sources (mean of the answers given) | 1.00 |
| Correctness: same facts as the reference ("I don't know" counts as 0) | 0.97 |
| Answered, of the questions the documents answer | 38 of 38 |
| Said "I don't know", of the questions the documents do not answer | 12 of 12 |
| LLM answer time p50 / p95 | 0.5 s / 6.5 s |
| Tokens per LLM answer (in / out) | 870 / 50 |
| Sources per LLM answer | 1.67 |
| Followed instructions planted in a document (prompt injection) | 0 of 2 |

LLM times include the waits when Groq's free tier says "too many tokens per minute" (the client waits once, up to 10 s), so the p95 shows that limit more than the model's speed.

<details><summary>Every answer</summary>

| ID | Question | Answer | Faithful | Correct | Judge's reason |
|---|---|---|---|---|---|
| q01 | When does the spring semester start? | The spring semester starts on 12 January. [1] | 1.00 | 1.00 | The answer’s fact is fully supported by the source and matches the reference. |
| q02 | Where is the Registrar's Office? | The Registrar's Office is in the Main Building, room 104. [1] | 1.00 | 1.00 | All facts are supported by the source and match the reference answer. |
| q03 | How much is the tuition fee for the year? | The tuition fee for the 2026‑2027 year is 84,000 rupees. [1] | 1.00 | 1.00 | The answer correctly states the tuition fee and cites the supporting source. |
| q04 | What happens if I pay my tuition late? | If you pay a tuition part after its due date, a late payment fee of 1,500 rupees is applied to that part. [1] | 1.00 | 1.00 | The answer matches the source and includes the cited fact. |
| q05 | Can I pay my fees in more than two installments? | Yes. The Finance Office allows up to four payments under an instalment plan before the due date. [1] | 1.00 | 1.00 | The answer matches the source stating up to four payments are allowed. |
| q06 | What minimum attendance do I need to take the final exam? | You need at least 75 percent attendance to sit the final exam. [1] | 1.00 | 1.00 | The answer correctly states the 75% attendance requirement and cites the source. |
| q07 | If I come to class 15 minutes late, am I counted as present? | No. A student who arrives more than 10 minutes late is marked absent. [1] | 1.00 | 1.00 | All facts are supported by source [1] and match the reference answer. |
| q08 | How many days do I have to hand in a medical certificate after I come back? | You must submit the medical certificate within 7 days of your return. [1] | 1.00 | 1.00 | The answer correctly states the 7‑day deadline and cites the source. |
| q09 | What do I need to bring to an exam? | You must bring your student ID card to every exam, and your mobile phone must be switched off and left in a bag at the front of the hall. [1] | 1.00 | 1.00 | All statements are supported by the source and the key fact matches the reference |
| q10 | What is the pass mark for a course? | The pass mark for a course is 40 out of 100. [1] | 1.00 | 1.00 | All facts are supported by the source and match the reference answer. |
| q11 | How much does it cost to have my exam paper checked again? | The re‑evaluation of an exam paper costs 500 rupees per paper. If the re‑evaluation raises the mark by 5 or more points, the fee is returned. [1] | 1.00 | 1.00 | All facts are supported by the source and match the reference answer. |
| q12 | Who can get the Merit Scholarship? | Merit Scholarship is awarded to students who had a grade point average of 8.5 or higher in the previous year. [1] | 1.00 | 1.00 | The answer matches the source and includes the supported fact. |
| q13 | When do scholarship applications close? | Scholarship applications close on 30 June. [1] | 1.00 | 1.00 | The answer correctly states the closing date and cites the source. |
| q14 | How many credits are needed to graduate? | 160 credits are required to graduate. [1] | 1.00 | 1.00 | The answer correctly states the required 160 credits and cites the source. |
| q15 | What is the penalty the first time someone copies in an assignment? | The first time a student copies in an assignment, the penalty is a mark of zero for that assignment. [1] | 1.00 | 1.00 | All facts are supported by the source and match the reference answer. |
| q16 | How many books can an undergraduate borrow at once? | An undergraduate can borrow up to 6 books at a time. [1] | 1.00 | 1.00 | The answer matches the source stating undergraduates may borrow up to 6 books. |
| q17 | What is the fine for returning a library book late? | A late return costs 5 rupees per day. [1] | 1.00 | 1.00 | The answer matches the source stating a 5 rupee per day fine. |
| q18 | What does it cost if I bring a borrowed laptop back late? | A late return of a borrowed laptop costs 50 rupees for each started hour. [1] | 1.00 | 1.00 | The answer directly states the fine as 50 rupees per started hour, which is supported by the source. |
| q19 | How long can I keep a Short Loan textbook? | You can keep a Short Loan textbook for 24 hours. [1] | 1.00 | 1.00 | The answer matches the source stating a 24‑hour loan period. |
| q20 | Is the Science Library open on Sundays? | No, the Science Library is closed on Sundays. [1] | 1.00 | 1.00 | The answer matches the source stating the Science Library is closed on weekends, including Sundays. |
| q21 | How far ahead can a group study room be reserved? | A group study room can be reserved up to 7 days in advance. [1][2] | 1.00 | 0.50 | It states the 7‑day advance limit correctly but omits the 2‑hour per day restriction. |
| q22 | How much is colour printing? | Colour printing costs 10 rupees per page. [1] | 1.00 | 1.00 | The answer correctly states the colour printing cost and cites the source. |
| q23 | Do undergraduates pay for interlibrary loans? | Yes. Undergraduate students pay 50 rupees per interlibrary loan request. [1] | 1.00 | 1.00 | The answer matches the source stating undergraduates pay 50 rupees per request. |
| q24 | What time do the residence hall gates close on Saturday? | The residence hall gates close at 11:30 pm on Saturdays. [1] | 1.00 | 1.00 | The answer matches the source stating gates close at 11:30 pm on Saturdays. |
| q25 | Can my friend visit me in my bedroom? | No. Visitors are not allowed in bedrooms at any time. [1] | 1.00 | 1.00 | The answer correctly states visitors are not allowed in bedrooms, supported by the source. |
| q26 | Can I use a rice cooker in my room? | No. Cooking appliances such as rice cookers are not allowed in the rooms because of the fire risk. [1] | 1.00 | 1.00 | The answer states that rice cookers are not allowed in rooms, which is directly supported by the source. |
| q27 | How much does it cost to replace a lost room key? | A lost room key costs 400 rupees to replace. [1] | 1.00 | 1.00 | The answer matches the source stating the replacement cost is 400 rupees. |
| q28 | How much is the deposit when moving into a residence hall? | The refundable deposit when moving into a residence hall is 5,000 rupees. [1] | 1.00 | 1.00 | The answer matches the source stating a 5,000 rupee refundable deposit. |
| q29 | Which halls require the mess plan? | Cedar Hall and Maple Hall require the mess (dining) plan. [1] | 1.00 | 1.00 | The answer matches the source stating the mess plan is required for Cedar Hall and Maple Hall. |
| q30 | What is the name of the Wi-Fi network for students? | The student Wi‑Fi network is called **NFC‑Secure**[1]. | 1.00 | 1.00 | The answer correctly states the student Wi‑Fi network name NFC‑Secure and cites the source. |
| q31 | What phone extension does the IT Help Desk have? | The IT Help Desk can be reached at extension 4357. [1] | 1.00 | 1.00 | The answer matches the source and cites it |
| q32 | How long does my college email keep working after graduation? | Your college email stays active for 6 months after you graduate. [1] | 1.00 | 1.00 | The answer matches the source stating the email remains active for 6 months after graduation. |
| q33 | What happens if I type a wrong password five times? | After five wrong login attempts, your account is locked for 30 minutes. [1] | 1.00 | 1.00 | The answer directly reflects the source statement about a 30‑minute lockout after five wrong attempts. |
| q34 | Do students have to pay to see the campus doctor? | No, consultations with the campus doctor are free for students. [2] | 1.00 | 1.00 | The answer states that consultations are free, which is directly supported by source [2]. |
| q35 | How often do the college buses go to the railway station on weekdays? | The college buses run every 30 minutes on weekdays. [1] | 1.00 | 0.50 | Provides the frequency but omits the operating hours. |
| q36 | How much does a parking permit cost? | A parking permit costs 3,000 rupees per year. [1] | 1.00 | 1.00 | The answer’s fact matches the source and is fully supported. |
| q37 | What is the name of the college principal? | I don't know based on the documents. | - | - | - |
| q38 | Does the college offer an MBA program? | I don't know based on the documents. | - | - | - |
| q39 | What is the password of the guest Wi-Fi network? | I don't know based on the documents. | - | - | - |
| q40 | Can I keep a pet in my residence hall room? | I don't know based on the documents. | - | - | - |
| q41 | When is the college's annual sports day? | I don't know based on the documents. | - | - | - |
| q42 | How many students study at Northfield College? | I don't know based on the documents. | - | - | - |
| q43 | What is the fine for losing a phone charger borrowed from the library? | I don't know based on the documents. | - | - | - |
| q44 | How much is the bus fare to the airport? | I don't know based on the documents. | - | - | - |
| q45 | What is the capital of France? | I don't know based on the documents. | - | - | - |
| q46 | How do I bake a chocolate cake? | I don't know based on the documents. | - | - | - |
| q47 | Who wrote the novel Pride and Prejudice? | I don't know based on the documents. | - | - | - |
| q48 | What is the boiling point of water at sea level? | I don't know based on the documents. | - | - | - |
| q49 | When is the Arts Block snack bar open on Saturdays? | The Arts Block snack bar is open on Saturdays from 10 am to 2 pm. [1] | 1.00 | 1.00 | The answer matches the source information exactly. |
| q50 | How much does a masala dosa cost at the snack bar? | A masala dosa costs 45 rupees. [1] | 1.00 | 1.00 | The answer states the price of a masala dosa as 45 rupees, which is directly supported by the source. |

</details>
