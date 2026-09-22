clear all
set more off
args output_root
if `"`output_root'"' == "" local output_root "`c(pwd)'/_profile-large"
set seed 20260919
set obs 5500000
generate double x1 = runiform()
forvalues j = 2/12 {
    generate double x`j' = mod(x1 * `j' + _n / (5500000 + `j'), 1)
}
capture mkdir `"`output_root'"'
capture mkdir `"`output_root'/workspace-a"'
capture mkdir `"`output_root'/workspace-b"'
generate byte workspace_profile = 1
save `"`output_root'/workspace-a/input.dta"', replace
replace workspace_profile = 2
save `"`output_root'/workspace-b/input.dta"', replace
display "@@LARGE_PROFILE_READY"
exit, clear
