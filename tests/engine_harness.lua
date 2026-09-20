local fixture = dofile(os.getenv('FIXTURE_FILE'))
utf8 = nil -- IMAPFilter does not necessarily expose the standard utf8 library.
local real_dofile = dofile
local helpers = real_dofile('app/helpers.lua')
helpers.received_timestamp = function(value) return tonumber(value) end
function dofile(path)
    if path == '/app/helpers.lua' then return helpers end
    return real_dofile(path)
end
os.time = function() return fixture.now end
options = {}
local function hex(value) return (value:gsub('.', function(c) return string.format('%02x', c:byte()) end)) end
function regex_search(pattern, value)
    local process = assert(io.popen('python3 tests/regex_bridge.py ' .. hex(pattern) .. ' ' .. hex(value)))
    local output = process:read('*a')
    assert(process:close())
    return output:match('true') ~= nil
end
local result_mt = {}
result_mt.__index = {
    move_messages = function(self, destination)
        for _, message in ipairs(self) do print('MUTATION move ' .. message[2] .. ' ' .. destination.name) end
        return true
    end,
    delete_messages = function(self)
        for _, message in ipairs(self) do print('MUTATION delete ' .. message[2]) end
        return true
    end,
}
result_mt.__sub = function(a, b)
    local result = setmetatable({}, result_mt)
    for _, item in ipairs(a) do
        local found = false
        for _, other in ipairs(b) do if item[2] == other[2] then found = true end end
        if not found then table.insert(result, item) end
    end
    return result
end
local inbox = {}
inbox.select_all = function(self)
    local result = setmetatable({}, result_mt)
    for id in ipairs(fixture.messages) do table.insert(result, {self,id}) end
    return result
end
setmetatable(inbox, {__index = function(_, uid)
    local message = fixture.messages[uid]
    return {
        fetch_field = function(_, name) return message[name] end,
        fetch_date = function() return tostring(message.date or 'invalid') end,
        fetch_body = function() return message.body or '' end,
        fetch_flags = function() return message.flags or {} end,
    }
end})
function IMAP()
    return setmetatable({INBOX=inbox, list_all=function() return fixture.folders end}, {
        __index=function(_, name) return {name=name} end
    })
end
real_dofile('config.lua')
