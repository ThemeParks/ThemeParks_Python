# History

Reached as `tp.entity(id).history`. Both methods page for you and yield rows
as they arrive, so a resort's five years never has to fit in memory.

Ask a **park** id wherever you can. Both history endpoints answer a whole park
in one request, and a park-by-park backfill of a large resort costs around a
hundred times fewer calls than the same data fetched ride by ride.

::: themeparks._ergonomic.history.HistoryApi
    options:
      heading_level: 2

::: themeparks._ergonomic.history.AsyncHistoryApi
    options:
      heading_level: 2

::: themeparks.BudgetExhaustedError
    options:
      heading_level: 2
