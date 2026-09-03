#!/bin/sh
# Fail the image build unless every extension the schema creates is installed,
# and new enough. Postgres accepts `CREATE EXTENSION` against whatever version
# happens to be present, so a too-old one migrates cleanly and only misbehaves
# later; this is the only place that difference is caught.
#
# Reads packaging metadata, so it proves an extension is present and new
# enough, not that it loads — a control file beside an unusable shared object
# would still pass. Creating each extension needs a running server, which the
# first `migrate up` against this image does.
#
# Usage: assert_extensions.sh <extension-directory> [name=minimum-version ...]
# A bare name is checked for presence only, which is right for an extension
# that ships with the base image and moves with it rather than with this file.
set -eu

if [ "$#" -lt 2 ]; then
    echo "usage: $0 <extension-directory> [name=minimum-version ...]" >&2
    exit 2
fi

ext_dir=$1
shift

for spec in "$@"; do
    name=${spec%%=*}
    minimum=${spec#*=}
    control="$ext_dir/$name.control"

    if [ ! -f "$control" ]; then
        echo "missing extension: $name" >&2
        exit 1
    fi

    if [ "$minimum" = "$name" ]; then
        echo "$name present"
        continue
    fi

    # `name=` with nothing after it would compare against an empty string,
    # which every version satisfies — a gate that silently checks nothing.
    if [ -z "$minimum" ]; then
        echo "no minimum version given for $name" >&2
        exit 2
    fi

    installed=$(sed -n "s/^default_version *= *'\(.*\)'/\1/p" "$control")
    if [ -z "$installed" ]; then
        echo "cannot read a version for $name from $control" >&2
        exit 1
    fi

    echo "$name $installed"
    if ! dpkg --compare-versions "$installed" ge "$minimum"; then
        echo "$name $installed is below the required $minimum" >&2
        exit 1
    fi
done
