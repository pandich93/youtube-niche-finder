#!/bin/sh
# Scheduled Postgres backups for a hosted copy (multi-user plan P1-13).
#
# Runs as the `backup` service of docker-compose.prod.yml, in the same image as
# the database, so pg_dump always matches the server's version. Connection
# settings come from the standard PG* variables (PGHOST, PGUSER, PGPASSWORD,
# PGDATABASE) that the service sets.
#
#   backup.sh         every BACKUP_INTERVAL_HOURS: dump, check, rotate
#   backup.sh once    one backup right now, exit status says whether it worked
#
# Each dump (pg_dump custom format, /backups/<db>-YYYYmmdd-HHMMSS.dump) is
# restored into a scratch database on the same server and its tables counted
# before it is kept, so a dump that cannot be restored shows up in the logs
# today rather than on the day it is needed. Only the newest BACKUP_KEEP
# dumps are kept. A restart does not cause an extra dump: the schedule
# continues from the newest file.
set -eu

DIR=/backups
INTERVAL_HOURS=${BACKUP_INTERVAL_HOURS:-24}
KEEP=${BACKUP_KEEP:-14}
VERIFY=${BACKUP_VERIFY:-1}
CHECK_DB="${PGDATABASE}_restore_check"
# no "database ... does not exist, skipping" notices in the logs
export PGOPTIONS="-c client_min_messages=warning"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') backup: $*"; }

user_tables() {
	psql -d "$1" -tAc "SELECT count(*) FROM information_schema.tables
	                   WHERE table_type = 'BASE TABLE'
	                     AND table_schema NOT IN ('pg_catalog', 'information_schema')"
}

verify() {
	dropdb --if-exists "$CHECK_DB"
	createdb -T template0 "$CHECK_DB"
	ok=1
	if ! pg_restore --no-owner --exit-on-error -d "$CHECK_DB" "$1" 2>"$DIR/.restore-check.log"; then
		log "RESTORE CHECK FAILED for $(basename "$1"):"
		sed 's/^/    /' "$DIR/.restore-check.log"
		ok=0
	else
		want=$(user_tables "$PGDATABASE")
		got=$(user_tables "$CHECK_DB")
		if [ "$got" != "$want" ]; then
			log "RESTORE CHECK FAILED for $(basename "$1"): $got tables restored, $want in the database"
			ok=0
		else
			log "restore check passed: $got tables"
		fi
	fi
	rm -f "$DIR/.restore-check.log"
	dropdb --if-exists "$CHECK_DB"
	[ "$ok" = 1 ]
}

rotate() {
	# newest first; everything after the first KEEP goes
	ls -1t "$DIR/$PGDATABASE"-*.dump 2>/dev/null | tail -n +"$((KEEP + 1))" | while read -r old; do
		rm -f "$old"
		log "removed old $(basename "$old")"
	done
}

backup_once() {
	file="$DIR/$PGDATABASE-$(date '+%Y%m%d-%H%M%S').dump"
	# written under a temporary name, so a half-written dump never looks like a backup
	if ! pg_dump -Fc -f "$file.partial"; then
		rm -f "$file.partial"
		log "pg_dump FAILED"
		return 1
	fi
	if [ "$VERIFY" = 1 ] && ! verify "$file.partial"; then
		mv "$file.partial" "$file.failed"
		log "kept the dump as $(basename "$file").failed for inspection; it is not counted as a backup"
		return 1
	fi
	mv "$file.partial" "$file"
	log "saved $(basename "$file") ($(du -h "$file" | cut -f1))"
	rotate
}

seconds_since_newest() {
	newest=$(ls -1t "$DIR/$PGDATABASE"-*.dump 2>/dev/null | head -n 1)
	if [ -z "$newest" ]; then
		echo -1
	else
		echo $(($(date +%s) - $(stat -c %Y "$newest")))
	fi
}

mkdir -p "$DIR"

if [ "${1:-}" = once ]; then
	backup_once
	exit $?
fi

# PID 1 in the container: stop promptly on `docker compose stop`
trap 'log "stopping"; exit 0' TERM INT

period=$((INTERVAL_HOURS * 3600))
log "every ${INTERVAL_HOURS}h into $DIR, keeping the newest $KEEP"
until pg_isready -q; do sleep 2; done
while :; do
	age=$(seconds_since_newest)
	if [ "$age" -lt 0 ] || [ "$age" -ge "$period" ]; then
		backup_once || true
		wait_for=$period
	else
		wait_for=$((period - age))
		log "newest backup is $((age / 60)) min old; next one in $((wait_for / 60)) min"
	fi
	sleep "$wait_for" &
	wait $!
done
