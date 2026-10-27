# Execution boundary only. The experiment's script decides the EDA flow.
set sandbox [interp create -safe]
set journal [open commands.jsonl w]
set serial 0
proc json_string {s} {
    return "\"[string map [list \\ \\\\ \" \\" \n \\n \r \\r \t \\t] $s]\""
}
proc record {id name args state} {
    global journal
    set values {}
    foreach arg $args {lappend values [json_string $arg]}
    puts $journal "{\"id\":$id,\"name\":[json_string $name],\"args\":\[[join $values ,]\],\"status\":[json_string $state],\"time_ms\":[clock milliseconds]}"
    flush $journal
}
proc input_path {p {kind source}} {
    global allowed_inputs
    set n [file normalize $p]
    if {![info exists allowed_inputs($n)]} {error "path not allowed: $p"}
    if {$kind eq "source" && $allowed_inputs($n) ni {rtl tb constraints scripts}} {error "source path not allowed: $p"}
    return $p
}
proc output_path {p} {
    if {$p ni {design.bit post_route.v route.sdf timing.rpt clock_summary.rpt}} {error "output path not allowed: $p"}
}
proc script_source {p} {
    global sandbox serial
    input_path $p
    if {[file extension $p] ni {.do .tcl .sdc}} {error "script path not allowed: $p"}
    set f [open $p r]; set data [read $f]; close $f
    set id [incr serial]; record $id source [list $p] running
    set code [catch {interp eval $sandbox $data} value options]
    record $id source [list $p] [expr {$code ? "failed" : "passed"}]
    return -options $options $value
}
proc script_puts {args} {
    if {[llength $args] != 1} {error "puts accepts one message; file channels not allowed"}
    foreach line [split [lindex $args 0] \n] {puts "SCRIPT: $line"}
}
proc guard {name args} {
    global stage allowed_inputs
    switch -- $name {
        import_device {
            if {$args ne {eagle_s20.db -package BG256}} {error "device/package not allowed; use eagle_s20.db -package BG256"}
        }
        read_verilog {
            if {[llength $args] != 4 || [lindex $args 0] ne "-file" || [lindex $args 2] ne "-top" || ![regexp {^[A-Za-z_][A-Za-z0-9_]*$} [lindex $args 3]]} {error "read_verilog expects -file <list> -top <module>"}
            foreach p [lindex $args 1] {input_path $p; if {$allowed_inputs([file normalize $p]) ne "rtl"} {error "RTL path not allowed: $p"}}
        }
        set_pin_assignment {
            if {[llength $args] != 2 || ![regexp {^[A-Za-z_][A-Za-z0-9_]*(\[[0-9]+\])?$} [lindex $args 0]] || ![regexp {^\s*LOCATION=[A-Z][0-9]+;\s*IOSTANDARD=LVCMOS33;\s*$} [lindex $args 1]]} {error "set_pin_assignment expects port and LOCATION=pin; IOSTANDARD=LVCMOS33;"}
        }
        optimize_rtl - optimize_gate - place - route - all_inputs - all_outputs {
            if {[llength $args]} {error "$name expects no arguments"}
        }
        report_clock_summary {
            if {$args ne {-file clock_summary.rpt}} {error "report_clock_summary expects -file clock_summary.rpt; other output paths not allowed"}
        }
        report_timing {
            if {[llength $args] % 2} {error "report_timing expects option/value pairs"}
            foreach {flag value} $args {
                switch -- $flag {
                    -file {if {$value ne "timing.rpt"} {error "report_timing -file path not allowed; use timing.rpt"}}
                    -sdf {if {$value ne "route.sdf"} {error "report_timing -sdf path not allowed; use route.sdf"}}
                    -from - -to - -through - -max_paths {}
                    default {error "report_timing option not allowed: $flag"}
                }
            }
        }
        write_verilog {
            if {$args ni {{post_route.v -back} {-back post_route.v}}} {error "write_verilog expects post_route.v -back"}
        }
        bitgen {if {$args ne {-bit design.bit}} {error "bitgen expects -bit design.bit"}}
        vlib {if {[llength $args] != 1 || [lindex $args 0] ni {work rtl_reference}} {error "library not allowed"}}
        vlog {
            set lib work; set paths {}; set i 0
            while {$i < [llength $args]} {
                set a [lindex $args $i]; incr i
                switch -- $a {
                    -sv - -quiet {}
                    -work {set lib [lindex $args $i]; incr i; if {$lib ni {work rtl_reference}} {error "library not allowed"}}
                    default {
                        input_path $a any
                        if {[file extension $a] ni {.v .sv}} {error "HDL path not allowed: $a"}
                        lappend paths $a
                    }
                }
            }
            if {![llength $paths]} {error "vlog requires HDL files"}
            if {$stage eq "post_route"} {
                foreach p $paths {
                    if {$allowed_inputs([file normalize $p]) eq "rtl" && $lib ne "rtl_reference"} {error "post-route RTL helpers must use rtl_reference; work is reserved for the routed netlist"}
                    if {$allowed_inputs([file normalize $p]) ne "rtl" && $lib ne "work"} {error "post-route netlist/models/TB must use work"}
                }
            } elseif {$lib ne "work"} {error "functional simulation uses work"}
        }
        vsim {
            if {$stage eq "query" && [lindex $args 0] eq "-help"} {return}
            set tops {}; set libs {}; set sdf {}; set finish {}; set i 0
            while {$i < [llength $args]} {
                set a [lindex $args $i]; incr i
                switch -- $a {
                    -quiet - -novopt - -O0 - -O1 - -O2 - -O3 - -O4 - -O5 - +acc - +nowarnTFMPC {}
                    -suppress {
                        set codes [lindex $args $i]; incr i
                        foreach code [split [string map {" " ","} $codes] ,] {
                            if {$code ni {2685 2718 2182 8756}} {error "Only unconnected-port warning codes 2685,2718,2182,8756 may be suppressed; keep assertion, timing and SDF errors visible"}
                        }
                    }
                    -voptargs=+acc - -voptargs=+nowarnTFMPC {}
                    -voptargs {
                        set options [lindex $args $i]; incr i
                        foreach option $options {if {$option ni {+acc +nowarnTFMPC}} {error "voptargs accepts +acc or +nowarnTFMPC"}}
                    }
                    -t {set t [lindex $args $i]; incr i; if {$t ni {1ps ps 1ns ns}} {error "time resolution not allowed"}}
                    -onfinish {set finish [lindex $args $i]; incr i; if {$finish ne "exit"} {error "use -onfinish exit"}}
                    -L {set l [lindex $args $i]; incr i; if {$l ni {work rtl_reference}} {error "library not allowed"}; lappend libs $l}
                    -sdfmax {set sdf [lindex $args $i]; incr i; if {$sdf ne "/tb/dut=route.sdf" || $stage ne "post_route"} {error "SDF target not allowed; use /tb/dut=route.sdf in post_route"}}
                    default {if {$a ni {work.tb work.glbl}} {error "vsim argument not allowed: $a"}; lappend tops $a}
                }
            }
            if {$finish ne "exit" || "work.tb" ni $tops} {error "vsim requires -onfinish exit work.tb"}
            if {$stage eq "post_route" && ($sdf eq "" || "work.glbl" ni $tops || $libs ne {work rtl_reference})} {error "post-route requires -L work -L rtl_reference -sdfmax /tb/dut=route.sdf work.tb work.glbl"}
        }
        run {if {![regexp {^(-all|[0-9]+( (ps|ns|us|ms))?)$} $args]} {error "run expects -all or a duration"}}
        help - get_ports - get_clocks - get_pins - get_nets - get_cells - create_clock - create_generated_clock - set_input_delay - set_output_delay - set_false_path - set_multicycle_path - set_clock_uncertainty - set_clock_latency - set_max_delay - set_min_delay {}
        default {error "command not allowed: $name"}
    }
}
proc forward {name args} {
    global serial sandbox
    set id [incr serial]; record $id $name $args running
    set code [catch {guard $name {*}$args; uplevel #0 [linsert $args 0 $name]} value]
    record $id $name $args [expr {$code ? "failed" : "passed"}]
    if {$code} {return -code error $value}
    return $value
}
proc safe_sdc {path} {
    global serial
    input_path $path
    # TD initializes timing queries in read_sdc. Evaluate editable constraints
    # in the safe interpreter after initializing it with an empty owned file.
    set id [incr serial]; record $id read_sdc [list $path] running
    set code [catch {read_sdc empty.sdc; script_source $path} value]
    record $id read_sdc [list $path] [expr {$code ? "failed" : "passed"}]
    if {$code} {return -code error $value}
    return $value
}
foreach name $commands {interp alias $sandbox $name {} forward $name}
interp alias $sandbox source {} script_source
interp alias $sandbox puts {} script_puts
if {$engine eq "td"} {interp alias $sandbox read_sdc {} safe_sdc}
foreach name {rtl_files tb_files td_models design_top} {interp eval $sandbox [list set $name [set $name]]}
set failure [catch {script_source $entry} message]
if {$failure} {puts "EDA_SCRIPT_ERROR: $message"} else {puts "EDA_SCRIPT_COMPLETED"}
close $journal
if {$engine eq "td"} {exit} else {quit -code [expr {$failure ? 1 : 0}]}
