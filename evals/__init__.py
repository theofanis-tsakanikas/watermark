"""The claim harnesses. Labelled, credential-free, and scored rather than asserted.

A test says the code does what it does. A harness here says *how many of a named set of
situations the system handled the way it said it would*, and prints the score. The difference
matters for the README: a scoreboard row is the output of one of these, not a summary of one.

Every case carries an expected outcome that is specific — a status, a count, a culprit — and
never "no exception was raised". A streaming system that raises no exception while publishing
nothing is indistinguishable from a correct one until somebody asks it which it is.
"""
