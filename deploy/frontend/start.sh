#!/bin/sh
set -eu

PLATFORM_DNS_RESOLVER=$(awk '/^nameserver/ { print $2; exit }' \
    /etc/resolv.conf)
export PLATFORM_DNS_RESOLVER
# shellcheck disable=SC2016
envsubst '${API_UPSTREAM} ${PLATFORM_DNS_RESOLVER}' \
    < /etc/nginx/platform.conf.template > /tmp/nginx.conf
exec nginx -c /tmp/nginx.conf -g 'daemon off;'
