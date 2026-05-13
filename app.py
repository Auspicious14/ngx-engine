import streamlit as st
from brain.query_engine import AlphaIntelligence

st.set_page_config(page_title="Alpha-Bot Intelligence", page_icon="🧠")

st.title("🇳🇬 NGX Intelligence Terminal")
st.markdown("Ask questions about the latest Nigerian Stock Market disclosures.")

# Initialize the brain (cached so it doesn't reload every time)
@st.cache_resource
def load_brain():
    return AlphaIntelligence()

brain = load_brain()

# UI Layout
query = st.text_input("Enter your query (e.g., 'What are the risks mentioned in the MTNN report?')")
company_filter = st.sidebar.text_input("Filter by Company (Optional)")

if st.button("Analyze"):
    if query:
        with st.spinner("Scanning Pinecone & Consulting Gemini..."):
            answer = brain.ask(query, company_filter=company_filter if company_filter else None)
            st.markdown("### Executive Summary")
            st.write(answer)
    else:
        st.warning("Please enter a question first.")
