import os
import google.generativeai as genai
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
        genai.configure(api_key=GEMINI_API_KEY)
        self.model = genai.GenerativeModel('gemini-2.0-flash')  # faster + cheaper than 1.5

    def ask(self, user_query: str, company_filter: str = None, category_filter: str = None):
        print(f"🔍 Query: {user_query}")

        query_vector = self.embeddings.embed_query(user_query)

        # Build filter — supports company AND/OR category
        search_filter = {}
        if company_filter:
            search_filter["company"] = company_filter
        if category_filter:
            search_filter["category"] = category_filter
        
        results = self.index.query(
            vector=query_vector,
            top_k=7,
            include_metadata=True,
            filter=search_filter if search_filter else None
        )

        if not results['matches']:
            return "⚠️ No relevant filings found for that query."

        context_segments = []
        for match in results['matches']:
            meta = match['metadata']
            context_segments.append(
                f"Source: {meta.get('filename', 'N/A')}\n"
                f"Company: {meta.get('company', 'N/A')}\n"
                f"Category: {meta.get('category', 'N/A')}\n"
                f"Title: {meta.get('title', 'N/A')}\n"
                f"Content: {meta.get('text', '')}\n"
            )
        
        full_context = "\n---\n".join(context_segments)

        prompt = f"""
        You are a specialized Nigerian Capital Market Intelligence Assistant.
        Use ONLY the retrieved disclosure segments below to answer the question.
        Always cite the company name and filing date in your answer.
        If the answer isn't in the context, say: "I don't have that specific data yet."

        RETRIEVED CONTEXT:
        {full_context}

        USER QUESTION:
        {user_query}

        EXECUTIVE SUMMARY:
        """

        response = self.model.generate_content(prompt)
        return response.text


if __name__ == "__main__":
    brain = AlphaIntelligence()

    # Test queries
    print(brain.ask("Tell me about recent director dealings at Zenith Bank."))
    print("---")
    print(brain.ask("Any recent financial results?", category_filter="Financial_Result"))
    print("---")
    print(brain.ask("Show insider dealing filings", category_filter="Insider_Dealing"))
