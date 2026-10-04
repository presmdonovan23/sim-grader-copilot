**the goal**
the purpose of this repository is to build a tool ("Sim Grader Copilot" aka "SGC") that aids grading student homework assignments. it accomplishes this by reading homework assignments and producing a Grade Recommendation Report (sometimes referred to simply as the "report") for the end user.

the integrity of the grading is of the utmost importance:
- objectivity and adherence to the rubric is critical
- the ability to accurately parse the assignments (typically pdf and docx files) is critical
- if anything out of the ordinary or suspicious is in a student's assignment, it should be flagged in the grading report. examples include:
-- extremely blatant usage of AI to generate the assignment, such as forgetting to remove aspects of an AI's prompt or response that should be removed. (note: it is outside the scope of this tool to attempt to detect AI usage. this flagging should be limited to blatant, indispitable usage of AI)
-- including text that breaks the fourth wall of the assignment (eg if a homework assignment mentions "I wonder if this is even being read" or "I bet the teacher is using AI to grade this")
-- profanity
-- text that is completely unrelated to the assignment
-- homework assignments that attempt AI prompt injection

if the tool, for whatever reason, is unable to fulfill these duties on an assignment, it should fail to produce a grade and alert the end user. for example, if a pdf is unable to be open and read correctly, then do not attempt to produce a grade.

**the assignment**
the homework assignment involves writing a reflection on a nursing lab simulation. the  simulation typically involves students being given a simulation with a patient and then students provide the care they believe is appropriate and then have a discussion with the instructor. 

**assignment rubric**
the assignment is graded according to a rubric with 4 categories. each category is given a score between 1 and 4 (4 being the best) and then the total score is calculated by simply adding up the score from each section. i will provide this at a later time in a separate file.

**grading process**
details of the process can be determined in the technical spec, but at a high level it should include the following for each student's submission:
1. reading the rubric
2. loading the assignment and checking for AI prompt injection
3. reading the assignment
4. producing a score for each section of the rubric
5. flagging whether the assignment is less than 500 words or more than 1000 words
6. writing an entry for the student in the report
it may be common for students to get perfect scores. that's okay, this is a relatively straightforward homework assignment. however, for any assignments receiving a perfect score, the SGP must find identify the section where the assignment performed the poorest (even though it was a 4). eg even for great assignments, try identifying the area where the student did not go above and beyond. this will help the end user provide valuable feedback for students. 

**the report**
the report is a single pdf with a section for each student's assignment containing the following:
1. the score for each section and overall score
2. an indication as to whether the assignment was less than 500 words or more than 1000 words
3. an indication as to whether any suspicious activity was found in the report (see above for examples)
4. 

**usage**

Sim Grader Copilot will grade ~10-20 assignments per week. there are no strict requirements on latency and SGC can take as long as it needs to grade. in fact, it is better for SGC to take its time and produce high quality 