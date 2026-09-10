#!/usr/bin/env bash
# Shared reader for etc/agencies.tsv. Sourced by other bin/ scripts, never
# executed directly.
#
# Row layout (6 tab-separated columns, `#` comments and blank lines skipped):
#   id <TAB> name <TAB> interval_sec <TAB> feed_url <TAB> static_url <TAB> ping_url
#
# `IFS=$'\t' read -r ...` cannot be used here. Tab is an IFS *whitespace*
# character, so a run of tabs collapses into a single delimiter and every
# column after an empty one shifts left: a row with an empty static_url (a
# real, supported configuration — an agency whose static GTFS is collected
# off-VM) would hand its ping_url to static_url and end up with no ping_url at
# all, i.e. an agency that fetches a healthcheck endpoint as if it were a GTFS
# zip and never pings the check that is supposed to notice it died. Translating
# tabs to a non-whitespace separator first is what keeps an empty column empty.

case "${0##*/}" in
    agencies-lib.sh)
        echo "agencies-lib.sh is a sourced helper, not a command" >&2
        exit 64
        ;;
esac

AGENCY_FIELD_SEP=$'\x1f'

# split_agency_row <row>
# Sets agency_id / agency_name / agency_interval / agency_feed_url /
# agency_static_url / agency_ping_url. Columns absent from a short row are set
# to the empty string, never left over from the previous row.
split_agency_row() {
    IFS="$AGENCY_FIELD_SEP" read -r agency_id agency_name agency_interval \
        agency_feed_url agency_static_url agency_ping_url \
        <<< "${1//$'\t'/$AGENCY_FIELD_SEP}"
}

# agency_row_is_data <row> — false for blank lines and `#` comments.
agency_row_is_data() {
    case "$1" in
        ''|'#'*) return 1 ;;
        *) return 0 ;;
    esac
}
