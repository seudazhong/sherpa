# Sherpa Postgres = pgvector + zhparser (ADR-036 Knowledge hybrid retrieval).
# Extends the pgvector image with SCWS (Simple Chinese Word Segmentation) and the
# zhparser text-search parser, so a `sherpa_text` TS config can segment Chinese for
# the lexical branch (the vector branch keeps using bge-m3 via ollama).
FROM pgvector/pgvector:pg16

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        build-essential git wget ca-certificates bzip2 \
        postgresql-server-dev-16; \
    rm -rf /var/lib/apt/lists/*

# SCWS 1.2.3 — zhparser's segmentation backend (bundles the dict).
RUN set -eux; \
    cd /tmp; \
    wget -q --tries=3 --timeout=30 http://www.xunsearch.com/scws/down/scws-1.2.3.tar.bz2; \
    tar xjf scws-1.2.3.tar.bz2; \
    cd scws-1.2.3; \
    ./configure --prefix=/usr/local/scws; \
    make -j"$(nproc)"; \
    make install; \
    echo "/usr/local/scws/lib" > /etc/ld.so.conf.d/scws.conf; \
    ldconfig

# zhparser extension (installs dict.utf8.xdb + rules into $PGSHARE/tsearch_data).
RUN set -eux; \
    cd /tmp; \
    git clone --depth 1 https://github.com/amutu/zhparser.git; \
    cd zhparser; \
    SCWS_HOME=/usr/local/scws make; \
    SCWS_HOME=/usr/local/scws make install; \
    rm -rf /tmp/scws-1.2.3* /tmp/zhparser
