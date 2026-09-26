import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, post } from "../api";
import { DataTable } from "../components/DataTable";
import { Badge, Card, ErrorNote, PageHead } from "../components/ui";
import { date, num } from "../format";

interface Dataset {
  exchange: string;
  symbol: string;
  timeframe: string;
  candles: number;
  start: string;
  end: string;
}

interface Job {
  id: string;
  status: string;
  error: string;
  result: unknown;
}

export default function DataPage() {
  const qc = useQueryClient();
  const data = useQuery({ queryKey: ["datasets"], queryFn: () => api<Dataset[]>("/api/data") });
  const [exchange, setExchange] = useState("binance");
  const [symbol, setSymbol] = useState("BTC/USDT");
  const [timeframe, setTimeframe] = useState("1h");
  const [since, setSince] = useState("2020-01-01");
  const [jobId, setJobId] = useState<string | null>(null);
  const start = useMutation({
    mutationFn: (body: Record<string, unknown>) => post<{ job: string }>("/api/data/download", body),
    onSuccess: (r) => setJobId(r.job),
  });
  const job = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => api<Job>(`/api/jobs/${jobId}`),
    enabled: !!jobId,
    refetchInterval: (q) => (q.state.data && q.state.data.status !== "running" ? false : 1500),
  });
  if (job.data && job.data.status === "done") qc.invalidateQueries({ queryKey: ["datasets"] });
  return (
    <>
      <PageHead title="Market data" sub="Local Parquet store. Higher timeframes are derived automatically from finer data (e.g. 4h from 1m)." />
      <div className="stack">
        <div className="grid grid-2">
          <Card title="Free research datasets" sub="No API key needed">
            <div className="stack">
              <p className="secondary">
                BTC/USD 1-minute candles from Bitstamp since 2012 (updated daily), and daily prices + market caps for ~45 coins from Coin Metrics — including
                delisted and collapsed coins, which keeps portfolio research honest about survivorship bias.
              </p>
              <div className="row">
                <button className="btn primary" disabled={start.isPending} onClick={() => start.mutate({ source: "free" })}>Download / refresh (~150 MB)</button>
              </div>
            </div>
          </Card>
          <Card title="Exchange candles via CCXT" sub="100+ exchanges; resumes from the last stored candle">
            <div className="stack">
              <div className="form-grid">
                <label className="field">Exchange<input className="input" value={exchange} onChange={(e) => setExchange(e.target.value)} /></label>
                <label className="field">Symbol<input className="input" value={symbol} onChange={(e) => setSymbol(e.target.value)} /></label>
                <label className="field">Timeframe
                  <select className="input" value={timeframe} onChange={(e) => setTimeframe(e.target.value)}>
                    {["1m", "5m", "15m", "1h", "4h", "1d"].map((t) => <option key={t}>{t}</option>)}
                  </select>
                </label>
                <label className="field">Since<input className="input" type="date" value={since} onChange={(e) => setSince(e.target.value)} /></label>
              </div>
              <div className="row">
                <button className="btn" disabled={start.isPending} onClick={() => start.mutate({ source: "ccxt", exchange, symbol, timeframe, since })}>Download</button>
              </div>
            </div>
          </Card>
        </div>
        <ErrorNote error={start.error} />
        {job.data && (
          <div className={`notice ${job.data.status === "error" ? "danger" : ""}`}>
            <div className="row">
              <Badge tone={job.data.status === "done" ? "good" : job.data.status === "error" ? "critical" : "neutral"} pulse={job.data.status === "running"}>
                {job.data.status}
              </Badge>
              <span className="mono">{job.data.id}</span>
            </div>
            {job.data.error && <div className="mt">{job.data.error}</div>}
            {job.data.status === "done" && <div className="pre mt">{JSON.stringify(job.data.result, null, 2)}</div>}
          </div>
        )}
        <Card title="Stored datasets" sub={`${data.data?.length ?? 0} series`} flush>
          <DataTable<Dataset>
            rows={data.data ?? []}
            rowKey={(d) => `${d.exchange}-${d.symbol}-${d.timeframe}`}
            maxHeight={560}
            empty="No data yet — download the free datasets above."
            columns={[
              { key: "exchange", label: "Source" },
              { key: "symbol", label: "Symbol" },
              { key: "timeframe", label: "Timeframe" },
              { key: "candles", label: "Candles", align: "right", render: (d) => num(d.candles, 0) },
              { key: "start", label: "From", render: (d) => date(Date.parse(d.start)) },
              { key: "end", label: "To", render: (d) => date(Date.parse(d.end)) },
            ]}
          />
        </Card>
      </div>
    </>
  );
}
