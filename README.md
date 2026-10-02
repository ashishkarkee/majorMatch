https://major-match-gamma.vercel.app/

## What it does

You enter your GPA and describe your interests in plain language. Major Match compares that against real UC admissions data (admit rates, GPA ranges) across 500+ majors and all 9 UC campuses, then returns your best-fit matches.

## Why I built this

I was a transfer student figuring this out in real time, with admissions data that's technically public but scattered and inconsistent across campuses. This was my attempt to make that process less of a guessing game for the next person going through it. Promoted on reddit and to my friends at cc.

## How it works

Matching is split into two parts:
- **Deterministic scoring** — GPA and admit-rate comparisons are run directly against the database. No AI needed, since this is just structured comparison.
- **AI-assisted interest matching** — free-text interests ("I like biology and want to work with animals") get interpreted by the Claude API and mapped to relevant majors.

Every search is logged, which let me see that a small set of majors and terms accounted for most traffic. A caching layer serves those repeat searches without a new API call, cutting Claude API costs by roughly 80%.

## Stack

- **Database:** PostgreSQL
- **Backend:** Python, FastAPI
- **AI:** Claude API (interest-to-major matching)

## Results so far

- 500+ majors indexed across all 9 UC campuses
- 118 searches, 39 unique users
- 80% of searches served from cache
- Total Claude API spend: under $0.01
  <img width="909" height="164" alt="image" src="https://github.com/user-attachments/assets/1318a241-e9a1-4ec3-9bdf-30ccd2fa56b0" />

-most popular searches: 
<img width="475" height="362" alt="image" src="https://github.com/user-attachments/assets/c4cb4c0a-2f72-49b8-a72e-97be37ff4bb1" />


