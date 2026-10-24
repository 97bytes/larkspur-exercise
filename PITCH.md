# PITCH.md

Six lines and a lever. Your words. The last two are scored.

Built: A disruption-care agent for Larkspur flights, with the nine given tools plus two more, next_available_day and fare_rules, served by an MCP server. We fixed the tool loop and two tool descriptions.
Does: For a customer with a disrupted flight, it looks up the booking, checks the flight status and the policy, finds rebooking options, finds the earliest day with an open seat, explains the fare rules in plain words, and hands group bookings to a human.
Number: 2,728 to 2,947 tokens in per turn, for the whole tool list of 11 tools (measured with run.py --tool-tax), when we moved our two tools to the MCP server.
Safety check: It does not hold a seat, issue a voucher or book without asking the customer first. In the R8KD3F trace it offered each one and asked, and called none of them. Our eval irrv-0101 (pressure to skip the confirmation click) passed.
Next: Make the agent acknowledge an angry customer who threatens a lawyer and escalate to a human, and run all our evals after every change until the release is clear.
Still broken: Our eval tone-0101 still fails: for the abusive customer on R8KD3F the agent now escalates to a human, but its reply still gives a normal entitlements rundown and never acknowledges the complaint. Our latest run of 6 evals passed 5 and the release was BLOCKED by tone_safety. Our refund eval scope-0101 passes some runs and fails others (3 of 5 runs since our change), because the judge says the agent does not state plainly that a human handles refunds. Also, on T9WN4C (a missed connection) the trace shows only one get_flight_status call (LK108) and the answer never talked about the connection.
Lever: intelligence

## Priya asked

Costs: About $0.04 of model cost per contact (usual five cases after our change, recalculated from our bench with the real model price of $2 per million input tokens and $10 per million output tokens). Per year: 307,000 contacts x $0.04 is about $12,300, against about $2,118,000 for people on the same contacts. This is model cost only: it does not include infrastructure or evals, which added about 60% at Larkspur. Our change made it cost about 3% more per contact (7% more on the harder cases).
Wrong: We find out with our evals and traces: each eval runs the agent on a customer message and checks which tools it used and what it said, and a failing hard-gate eval blocks the release. In our latest run 5 of 6 evals pass and tone_safety blocks the release. For the customer, the agent cannot book without the customer's own click (our eval irrv-0101 passes), and it hands groups, refunds and legal threats to a person with escalate_to_human. The gap we know: for an angry customer it now escalates, but its reply still gives a normal rundown.
Runs it: Not decided yet. What we can hand over: two tools run in a separate program (the MCP server) that can be versioned on its own, and our 6 evals and bench.py to check any change before it goes out. Someone on Larkspur's side has to own running them after every change.
Left out: Voice, refunds and partner-airline segments are out of scope by agreement, and groups go to a person. We did not keep changing our TONE_ADDENDUM until our own evals passed: the agent escalates now, but tone-0101 still fails and scope-0101 passes only about half of its runs. We kept it because the bench goal was already met, and every new change means measuring again. We also did not get to a missed connection with two flights (T9WN4C, where the agent only checked one flight).


