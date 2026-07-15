-- Atomic sliding-window rate limiter.
--
-- Runs as a single Lua script inside Redis. Redis executes Lua scripts to
-- completion as one atomic unit — no other client's command, not even
-- another Lua script, can run in the middle of it. That's what makes this
-- immune to the check-then-act race that a separate ZCARD-then-ZADD from
-- the application would have: two concurrent requests both reading "9 of
-- limit 10, proceed" before either has recorded its own request.
--
-- KEYS[1] = the rate-limit key, e.g. rate:{tenant_id}:/proxy/get
-- ARGV[1] = now_ms          (current timestamp, milliseconds)
-- ARGV[2] = window_ms       (window size, milliseconds)
-- ARGV[3] = limit           (max requests allowed inside the window)
-- ARGV[4] = request_id      (unique member for this request's ZSET entry)
--
-- All variable values arrive via ARGV rather than being string-interpolated
-- into the script body: KEYS/ARGV is how Redis Cluster determines which
-- slot a script's keys belong to, and interpolating values into the script
-- text would change the script's hash on every call, defeating EVALSHA's
-- caching (Redis would recompile the script from scratch every time).

local key = KEYS[1]
local now_ms = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local request_id = ARGV[4]

-- Step 1: discard log entries older than the window.
redis.call('ZREMRANGEBYSCORE', key, 0, now_ms - window_ms)

-- Step 2: count what remains inside the window.
local current_count = redis.call('ZCARD', key)

if current_count < limit then
    -- Step 3: record this request and allow it.
    redis.call('ZADD', key, now_ms, request_id)
    -- PEXPIRE, not EXPIRE: EXPIRE only accepts whole-second precision, which
    -- would silently round a sub-second window (a high-sensitivity route's
    -- policy might use one) up to a full second.
    redis.call('PEXPIRE', key, window_ms)
    return {1, limit - current_count - 1} -- {allowed, remaining}
else
    return {0, 0} -- {blocked, remaining}
end
