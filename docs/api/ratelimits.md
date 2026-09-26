# Rate limits

The API meters requests per minute, and history requests again per hour. Both
are read off every response that carries them and exposed on the client.

`None` means the server did not say, never "nothing left". An unmetered plan
advertises no figures, and neither does a publicly cacheable response, because
the numbers are per-caller and a shared cache would hand one caller's budget to
another. Anonymous calls therefore carry nothing; calls made with a key do.

::: themeparks.RateLimits
    options:
      heading_level: 2

::: themeparks.RateLimit
    options:
      heading_level: 2
