# Team objective

The team seeks a +0.5% return on its initial paper capital by the end of the session, within the
position limits, while producing the required company update and duty reports. The target is a
goal, not a guarantee.

Trading works like this: the team starts with cash and a zero position in the instrument. An
order sets an integer target position between -10 and +10 shares; hold keeps the current target
and close sets it to zero. Orders fill immediately at the current displayed price with a fee of
one tenth of one percent of the traded value. Final equity is cash plus position times the final
price.

Prices are exogenous in this simulator: they follow a fixed path that no agent action can move.
The publication feed does not affect fills or valuation.
