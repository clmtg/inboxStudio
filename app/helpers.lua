local function received_timestamp(value)
    if type(value) ~= 'string' then return nil end
    value = value:match('^%s*"(.-)"%s*$') or value
    local day, month, year, hour, minute, second, zone = value:match(
        '^%s*(%d%d?)%-(%a%a%a)%-(%d%d%d%d) (%d%d):(%d%d):(%d%d) ([+-]%d%d%d%d)%s*$')
    local months = {jan=1, feb=2, mar=3, apr=4, may=5, jun=6,
                    jul=7, aug=8, sep=9, oct=10, nov=11, dec=12}
    local month_number = month and months[month:lower()]
    if not month_number then return nil end
    local command = string.format(
        "LC_ALL=C date --date='%s-%02d-%s %s:%s:%s %s' +%%s 2>/dev/null",
        year, month_number, day, hour, minute, second, zone)
    local process = io.popen(command)
    if not process then return nil end
    local result = tonumber(process:read('*a'))
    process:close()
    return result
end


-- IMAPFilter may omit Lua's utf8 library. Encode these legacy-charset code
-- points directly using only the string and math libraries.
local function encode_utf8(code)
    if code < 128 then return string.char(code) end
    if code < 2048 then
        return string.char(192 + math.floor(code / 64), 128 + code % 64)
    end
    return string.char(224 + math.floor(code / 4096),
        128 + math.floor(code / 64) % 64, 128 + code % 64)
end

local windows1252 = {
    [128]=0x20AC, [130]=0x201A, [131]=0x0192, [132]=0x201E,
    [133]=0x2026, [134]=0x2020, [135]=0x2021, [136]=0x02C6,
    [137]=0x2030, [138]=0x0160, [139]=0x2039, [140]=0x0152,
    [142]=0x017D, [145]=0x2018, [146]=0x2019, [147]=0x201C,
    [148]=0x201D, [149]=0x2022, [150]=0x2013, [151]=0x2014,
    [152]=0x02DC, [153]=0x2122, [154]=0x0161, [155]=0x203A,
    [156]=0x0153, [158]=0x017E, [159]=0x0178,
}

local function decoded_subject(raw)
    if not raw then return nil end
    raw = raw:gsub('\r?\n[ \t]+', ' ')
    raw = raw:gsub('(%?=)[ \t]+(=%?)', '%1%2')
    local valid = true
    local alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'
    local decoded = raw:gsub('=%?([^?]+)%?([bBqQ])%?([^?]*)%?=', function(charset, encoding, data)
        if encoding:lower() == 'q' then
            data = data:gsub('_', ' '):gsub('=(%x%x)', function(hex)
                return string.char(tonumber(hex, 16))
            end)
        else
            local buffer, bits, output = 0, 0, {}
            for c in data:gmatch('.') do
                if c ~= '=' then
                    local index = alphabet:find(c, 1, true)
                    if not index then valid = false; return '' end
                    buffer, bits = buffer * 64 + index - 1, bits + 6
                    if bits >= 8 then
                        bits = bits - 8
                        output[#output + 1] = string.char(math.floor(buffer / 2^bits))
                        buffer = buffer % 2^bits
                    end
                end
            end
            data = table.concat(output)
        end
        charset = charset:lower()
        if charset == 'iso-8859-1' or charset == 'windows-1252' then
            data = data:gsub('[\128-\255]', function(c)
                local code = c:byte()
                if charset == 'windows-1252' and code < 160 then
                    code = windows1252[code]
                    if not code then valid = false; return '' end
                end
                return encode_utf8(code)
            end)
        elseif charset ~= 'utf-8' and charset ~= 'us-ascii' then
            valid = false
        end
        return data
    end)
    if not valid or decoded:find('=?', 1, true) then return nil end
    return decoded:gsub('\194\160', ' ')
end


-- Match the mailbox address, never a display name containing an address.
-- Ambiguous/multiple mailboxes are left untouched rather than guessed.
local function sender_email(value)
    if type(value) ~= 'string' then return nil end
    value = value:gsub('^[Ff][Rr][Oo][Mm]:%s*', ''):gsub('[\r\n]+%s*', ' ')
    local address
    if value:find('<', 1, true) then
        address = value:match('^[^<>]*<([^<>]+)>%s*$')
    else
        address = value:match('^%s*([^%s<>]+)%s*$')
    end
    if not address or not address:match('^[^%s<>@,;]+@[^%s<>@,;]+%.[^%s<>@,;]+$') then return nil end
    return address:lower()
end

return {received_timestamp=received_timestamp, decoded_subject=decoded_subject, sender_email=sender_email}
