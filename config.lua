-- IMAPFilter execution engine. Edit rules in the web UI, not in this file.
io.stdout:setvbuf('no')
local document = dofile(os.getenv('RULES_FILE') or '/tmp/active-rules.lua')
local helpers = dofile(os.getenv('HELPERS_FILE') or '/app/helpers.lua')
local dry_run = document.settings.preview
options.timeout = 60
options.certificates = false
options.hostnames = true
options.create = false
options.subscribe = false
options.info = false

local account = IMAP {
    server = os.getenv('IMAP_HOST') or 'imap.mail.me.com', port = tonumber(os.getenv('IMAP_PORT') or '993'), ssl = 'auto',
    username = assert(os.getenv('IMAP_USERNAME'), 'Missing IMAP_USERNAME'),
    password = assert(os.getenv('IMAP_PASSWORD'), 'Missing IMAP_PASSWORD'),
}
local folders = assert(account:list_all('', '*'), 'Unable to list iCloud folders')
local folder_set = {}
for _, name in ipairs(folders) do
    folder_set[name] = true
    print('FOLDER\t' .. name)
end
print('Connected to mail server. Mode: ' .. (dry_run and 'PREVIEW' or 'LIVE'))
local inbox = assert(account.INBOX:select_all(), 'Unable to list Inbox')
print('INBOX_COUNT\t' .. #inbox)
local now = os.time()
local cache = {}

local function literal_pattern(value)
    value = value:gsub('\194\160', ' ')
    value = value:gsub('([\\%.%+%-%*%?%[%]%^%$%(%)%{%}%|])', '\\%1')
    return '(*UTF)(?i)' .. value:gsub('%s+', '\\s+')
end

local function matches_text(text, value)
    return regex_search(literal_pattern(value), text)
end

local function match_condition(message, condition)
    local mailbox, uid = table.unpack(message)
    cache[uid] = cache[uid] or {}
    local cached = cache[uid]
    local field, op, value = condition.field, condition.op, condition.value
    if field == 'flagged' then
        if cached.flags == nil then cached.flags = mailbox[uid]:fetch_flags() or false end
        if not cached.flags then return nil end
        for _, flag in ipairs(cached.flags) do
            if flag:lower() == '\\flagged' then return true end
        end
        return false
    end
    if field == 'age_hours' then
        if cached.received == nil then
            cached.received = helpers.received_timestamp(mailbox[uid]:fetch_date()) or false
        end
        if not cached.received then return nil end
        local older = cached.received < now - value * 3600
        if op == 'older_than' then return older else return not older end
    end
    if field == 'sender_email' then
        if cached.sender == nil then cached.sender = mailbox[uid]:fetch_field('from') or false end
        local email = helpers.sender_email(cached.sender)
        if not email then return nil end
        if op == 'is' then return email == value:lower() end
        return email:find(value:lower(), 1, true) ~= nil
    end
    if field == 'sender_domain' then
        if cached.sender == nil then cached.sender = mailbox[uid]:fetch_field('from') or false end
        if not cached.sender then return nil end
        local domain = value:lower():gsub('%.', '\\.')
        return regex_search('(?i)@(?:[a-z0-9-]+\\.)*' .. domain .. '(?:>\\s*|\\s*)$', cached.sender)
    end
    local found = false
    if field == 'subject' or field == 'subject_or_body' then
        if cached.subject == nil then
            cached.subject = helpers.decoded_subject(mailbox[uid]:fetch_field('subject')) or false
        end
        if not cached.subject then return nil end
        found = matches_text(cached.subject, value)
    end
    if field == 'body' or (field == 'subject_or_body' and not found) then
        if cached.body == nil then cached.body = mailbox[uid]:fetch_body() or false end
        if not cached.body then return nil end
        local pattern = literal_pattern(value):gsub('^%(%*UTF%)', '')
        found = regex_search(pattern, cached.body)
    end
    if op == 'not_contains' then return not found else return found end
end

-- Any succeeds on a definite match; otherwise unreadable alternatives block fallbacks.
-- Keep legacy All short-circuit behavior unchanged.
local function matches(message, conditions, mode)
    if mode == 'any' then
        local unknown = false
        for _, condition in ipairs(conditions) do
            local result = match_condition(message, condition)
            if result == true then return true end
            if result == nil then unknown = true end
        end
        if unknown then return nil end
        return false
    end
    for _, condition in ipairs(conditions) do
        local result = match_condition(message, condition)
        if result == nil then return nil end
        if not result then return false end
    end
    return true
end

-- Every action can have an age gate measured from the received timestamp.
local function action_ready(message, action)
    if action.delay_hours == nil then return true end
    local mailbox, uid = table.unpack(message)
    cache[uid] = cache[uid] or {}
    local cached = cache[uid]
    local waiting_reason = 'age'
    -- Read mail can leave Inbox on the next scan without waiting for its age.
    if action.action == 'move' or action.action == 'move_after' then
        if cached.flags == nil then cached.flags = mailbox[uid]:fetch_flags() or false end
        waiting_reason = cached.flags and 'unread' or 'flags_unknown'
        for _, flag in ipairs(cached.flags or {}) do
            if flag:lower() == '\\seen' then return true end
        end
    end
    if cached.received == nil then
        cached.received = helpers.received_timestamp(mailbox[uid]:fetch_date()) or false
    end
    if not cached.received then return nil end
    return cached.received < now - action.delay_hours * 3600, waiting_reason
end

local selections = {}
local diagnostics = {}
for _, rule in ipairs(document.rules) do
    diagnostics[rule.id] = {evaluated=0, matched=0, unreadable=0}
    local buckets = {}
    if rule.action == 'conditional' then
        for i, branch in ipairs(rule.branches) do
            buckets[i] = {action=branch, messages=inbox-inbox, label='branch ' .. i}
        end
        buckets[#buckets+1] = {action=rule.otherwise, messages=inbox-inbox, label='otherwise'}
    else
        buckets[1] = {action=rule, messages=inbox-inbox, label=''}
    end
    for _, bucket in ipairs(buckets) do
        bucket.waiting = {age=0, unread=0, flags_unknown=0}
    end
    selections[rule.id] = buckets
end
local skipped = 0
for _, message in ipairs(inbox) do
    for _, rule in ipairs(document.rules) do
        if rule.enabled then
            local stats = diagnostics[rule.id]
            stats.evaluated = stats.evaluated + 1
            local matched = matches(message, rule.conditions, rule.match)
            if matched == nil then stats.unreadable=stats.unreadable+1; skipped=skipped+1; break end
            if matched then
                stats.matched = stats.matched + 1
                local buckets = selections[rule.id]
                local selected = 1
                if rule.action == 'conditional' then
                    selected = #buckets
                    for i, branch in ipairs(rule.branches) do
                        local branch_match = matches(message, branch.conditions, branch.match)
                        if branch_match == nil then selected=nil; break end
                        if branch_match then selected=i; break end
                    end
                end
                if not selected then stats.unreadable=stats.unreadable+1; skipped=skipped+1; break end
                local bucket = buckets[selected]
                local ready, reason = action_ready(message, bucket.action)
                if ready == nil then stats.unreadable=stats.unreadable+1; skipped=skipped+1; break end
                if not ready then
                    bucket.waiting[reason] = bucket.waiting[reason] + 1
                    break -- Reserve waiting mail against later rules.
                end
                if ready then
                    table.insert(bucket.messages, message)
                    if bucket.action.action ~= 'continue' then break end
                end
            end
        end
    end
end
-- Check every selected destination before any mailbox changes.
for _, rule in ipairs(document.rules) do
    for _, bucket in ipairs(selections[rule.id]) do
        if #bucket.messages > 0 and (bucket.action.action == 'move' or bucket.action.action == 'move_after' or bucket.action.action == 'trash') then
            assert(folder_set[bucket.action.folder], 'Destination folder does not exist: ' .. bucket.action.folder)
        end
    end
end
for _, rule in ipairs(document.rules) do
    if rule.enabled then
        local total = 0
        for _, bucket in ipairs(selections[rule.id]) do
            local action, messages = bucket.action, bucket.messages
            total = total + #messages
            if not dry_run and #messages > 0 then
                if action.action == 'move' or action.action == 'move_after' or action.action == 'trash' then
                    assert(messages:move_messages(account[action.folder]), 'Move failed: ' .. rule.name)
                elseif action.action == 'delete' then
                    assert(messages:delete_messages(), 'Delete failed: ' .. rule.name)
                end
            end
            local label = rule.name .. (bucket.label ~= '' and ' / ' .. bucket.label or '')
            local waiting = bucket.waiting
            local detail = ''
            if waiting.age + waiting.unread + waiting.flags_unknown > 0 then
                detail = string.format(' · waiting for age: %d (server reports unread: %d; read status unavailable: %d)',
                    waiting.age + waiting.unread + waiting.flags_unknown, waiting.unread, waiting.flags_unknown)
            end
            print(string.format('%s: %d messages · %s%s%s', label, #messages, action.action,
                dry_run and ' (preview only)' or '', detail))
        end
        local stats = diagnostics[rule.id]
        print(string.format('%s: evaluated %d; conditions matched %d; unreadable %d',
            rule.name, stats.evaluated, stats.matched, stats.unreadable))
        print('RULE_RESULT\t' .. rule.id .. '\t' .. total)
    end
end
if skipped > 0 then print('Left unchanged due to unreadable conditions: ' .. skipped) end
print('IMAPFilter check completed')
