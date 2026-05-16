import os
import json
from google import genai
from google.genai import types
from pinecone import Pinecone
from langchain_huggingface import HuggingFaceEmbeddings

PINECONE_API_KEY = os.environ["PINECONE_API_KEY"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
INDEX_NAME = "ngx-brain"

class AlphaIntelligence:
    def __init__(self):
        self.pc = Pinecone(api_key=PINECONE_API_KEY)
        self.index = self.pc.Index(INDEX_NAME)
        self.embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
        self.client = genai.Client(api_key=GEMINI_API_KEY)

        self.system_prompt = """
        You are Alpha, a specialized Nigerian Capital Market Intelligence Assistant.
        You have access to two data sources:
        1. NGX corporate disclosure documents (PDFs) via vector search
        2. Live stock price and volume data via SQL database

        Routing rules:
        - Document questions (financial results, AGM, appointments, corporate actions) → search_disclosures
        - Insider/director dealing questions → get_insider_activity
        - Market/price/momentum questions (which stock to buy, top gainers) → get_market_leaders
        - Unusual activity questions → get_volume_spikes
        - Complex questions → call multiple tools and synthesize

        Always cite company names and dates. Be concise and actionable.
        If data isn't available from any tool, say so clearly.
        """

        self.tools = [
            types.Tool(function_declarations=[
                types.FunctionDeclaration(
                    name="search_disclosures",
                    description="Search NGX corporate disclosure PDFs. Use for questions about financial results, AGM resolutions, corporate actions, director appointments, or any document-based question.",
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "query": types.Schema(type=types.Type.STRING, description="Natural language search query"),
                            "company": types.Schema(type=types.Type.STRING, description="Optional company name filter e.g. 'ZENITH BANK PLC'"),
                            "category": types.Schema(type=types.Type.STRING, description="Optional category: Financial_Result, Insider_Dealing, Dividend_Announcement, General_Disclosure"),
                        },
                        required=["query"]
                    )
                ),
                types.FunctionDeclaration(
                    name="get_insider_activity",
                    description="Get recent insider dealing and director transaction disclosures. Use for questions about insider trading, director share purchases or sales.",
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "company": types.Schema(type=types.Type.STRING, description="Optional company name filter"),
                        },
                        required=[]
                    )
                ),
                types.FunctionDeclaration(
                    name="get_market_leaders",
                    description="Query live stock price database for top performing stocks by percentage change. Use for questions about which stocks are growing, market trends, or investment opportunities.",
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "days": types.Schema(type=types.Type.INTEGER, description="Lookback period in days (default 30)"),
                            "limit": types.Schema(type=types.Type.INTEGER, description="Number of stocks to return (default 10)"),
                        },
                        required=[]
                    )
                ),
                types.FunctionDeclaration(
                    name="get_volume_spikes",
                    description="Detect unusual trading volume spikes in the NGX market. Use for questions about unusual activity, breakouts, or momentum signals.",
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "threshold": types.Schema(type=types.Type.NUMBER, description="Volume ratio threshold (default 2.0 = 2x average)"),
                            "days": types.Schema(type=types.Type.INTEGER, description="Lookback period in days (default 7)"),
                        },
                        required=[]
                    )
                ),
            ])
        ]

        self.tool_map = {
            "search_disclosures": self._search_disclosures,
            "get_insider_activity": self._get_insider_activity,
            "get_market_leaders": self._get_market_leaders,
            "get_volume_spikes": self._get_volume_spikes,
        }

    # ── Tool implementations ──────────────────────────────────────────────

    def _search_disclosures(self, query: str, company: str = None, category: str = None) -> str:
        query_vector = self.embeddings.embed_query(query)

        search_filter = {}
        if company:
            search_filter["company"] = company
        if category:
            search_filter["category"] = category

        results = self.index.query(
            vector=query_vector,
            top_k=7,
            include_metadata=True,
            filter=search_filter if search_filter else None
        )

        if not results["matches"]:
            return "No relevant disclosure documents found."

        segments = []
        for match in results["matches"]:
            meta = match["metadata"]
            segments.append(
                f"Source: {meta.get('filename', 'N/A')}\n"
                f"Company: {meta.get('company', 'N/A')}\n"
                f"Category: {meta.get('category', 'N/A')}\n"
                f"Title: {meta.get('title', 'N/A')}\n"
                f"Date: {meta.get('date_submitted', 'N/A')}\n"
                f"Content: {meta.get('text', '')}"
            )
        return "\n---\n".join(segments)

    def _get_insider_activity(self, company: str = None) -> str:
        return self._search_disclosures(
            query="director dealing insider transaction shares bought sold",
            company=company,
            category="Insider_Dealing"
        )

    def _get_market_leaders(self, days: int = 30, limit: int = 10) -> str:
        """Replace the query below with your actual schema."""
        try:
            import psycopg2
            conn = psycopg2.connect(os.environ["DATABASE_URL"])
            cursor = conn.cursor()
            cursor.execute("""
                SELECT symbol, ROUND(AVG(percent_change)::numeric, 2) as avg_change,
                       SUM(volume) as total_volume
                FROM stock_prices
                WHERE trade_date > NOW() - INTERVAL '%s days'
                GROUP BY symbol
                ORDER BY avg_change DESC
                LIMIT %s
            """, (days, limit))
            rows = cursor.fetchall()
            conn.close()

            if not rows:
                return "No stock price data found."

            result = f"Top {limit} performers over {days} days:\n"
            for symbol, avg_change, volume in rows:
                result += f"  {symbol}: avg {avg_change}% change, volume {volume:,}\n"
            return result
        except Exception as e:
            return f"DB error: {e}"

    def _get_volume_spikes(self, threshold: float = 2.0, days: int = 7) -> str:
        """Replace the query below with your actual schema."""
        try:
            import psycopg2
            conn = psycopg2.connect(os.environ["DATABASE_URL"])
            cursor = conn.cursor()
            cursor.execute("""
                SELECT symbol, trade_date, volume,
                       ROUND((volume / AVG(volume) OVER (PARTITION BY symbol))::numeric, 2) as volume_ratio
                FROM stock_prices
                WHERE trade_date > NOW() - INTERVAL '%s days'
                ORDER BY volume_ratio DESC
                LIMIT 10
            """, (days,))
            rows = cursor.fetchall()
            conn.close()

            if not rows:
                return "No volume spikes detected."

            result = "Volume spike alerts:\n"
            for symbol, date, volume, ratio in rows:
                result += f"  {symbol} on {date}: {ratio}x average volume ({volume:,})\n"
            return result
        except Exception as e:
            return f"DB error: {e}"

    # ── Agentic loop ──────────────────────────────────────────────────────

    def ask(self, user_query: str, company_filter: str = None, category_filter: str = None) -> str:
        print(f"🔍 Query: {user_query}")

        messages = [types.Content(
            role="user",
            parts=[types.Part(text=user_query)]
        )]

        config = types.GenerateContentConfig(
            system_instruction=self.system_prompt,
            tools=self.tools,
            temperature=0.2
        )

        response = self.client.models.generate_content(
            model="gemini-2.5-flash",
            contents=messages,
            config=config
        )

        # Agentic loop — keep executing tools until Gemini returns final text
        while True:
            parts = response.candidates[0].content.parts
            has_tool_call = any(hasattr(p, "function_call") and p.function_call for p in parts)

            if not has_tool_call:
                break

            tool_results = []
            for part in parts:
                if not (hasattr(part, "function_call") and part.function_call):
                    continue

                fn_name = part.function_call.name
                fn_args = dict(part.function_call.args)
                print(f"  🔧 {fn_name}({fn_args})")

                fn_result = self.tool_map[fn_name](**fn_args)

                tool_results.append(types.Part(
                    function_response=types.FunctionResponse(
                        name=fn_name,
                        response={"result": fn_result}
                    )
                ))

            messages.append(response.candidates[0].content)
            messages.append(types.Content(role="user", parts=tool_results))

            response = self.client.models.generate_content(
                model="gemini-2.5-flash",
                contents=messages,
                config=config
            )

        return response.text


if __name__ == "__main__":
    brain = AlphaIntelligence()

    tests = [
        "Tell me about recent director dealings at Zenith Bank.",
        "Any recent financial results?",
        "Show insider dealing filings",
        "Which stocks have the best momentum right now?",
        "Any unusual volume spikes this week?",
        "Based on market growth, which stock should I consider buying?",
    ]

    for q in tests:
        print(f"\n❓ {q}")
        print(brain.ask(q))
        print("─" * 60)
