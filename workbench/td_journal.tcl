# Native command journal for the legacy generated TD flow.
set wb_journal [open commands.jsonl w]
set wb_serial 0
proc wb_json_string {s} {
    return "\"[string map [list \\ \\\\ \" \\\" \n \\n \r \\r \t \\t] $s]\""
}
proc wb_record {id name args state} {
    global wb_journal
    set values {}
    foreach arg $args {lappend values [wb_json_string $arg]}
    puts $wb_journal "{\"id\":$id,\"name\":[wb_json_string $name],\"args\":\[[join $values ,]\],\"status\":[wb_json_string $state],\"time_ms\":[clock milliseconds]}"
    flush $wb_journal
}
proc wb_td {name args} {
    global wb_serial
    set id [incr wb_serial]
    wb_record $id $name $args running
    set code [catch {uplevel 1 [linsert $args 0 $name]} value options]
    wb_record $id $name $args [expr {$code ? "failed" : "passed"}]
    return -options $options $value
}
