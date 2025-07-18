import streamlit as st
import os
import json
import re
from typing import List, TypedDict, Dict, Any
from langchain_core.documents import Document
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_community.document_loaders import UnstructuredWordDocumentLoader
from langgraph.graph import START, StateGraph
from langchain_core.messages import HumanMessage
from langchain.schema import SystemMessage
from Rag_to_DB import create_database,main as Rag_to_DB 

# Page configuration
st.set_page_config(page_title="RAG-Powered Trackbot", page_icon="🤖", layout="wide")

def load_txt(file_path):
    """Load text content from a file."""
    try:
        with open(file_path, encoding='utf-8') as file:
            return file.read()
    except FileNotFoundError:
        print(f"File not found: {file_path}")
        return ""

# Load prompts from external files
clarification_prompt = SystemMessage(content=load_txt("clarification_prompt.txt"))
input_instruction = SystemMessage(content=load_txt("input_prompt.txt"))
Get_Json_prompt = SystemMessage(content=load_txt("Get_Json_prompt.txt"))
user_stories_prompt = SystemMessage(content=load_txt("user_stories_prompt.txt"))
business_rules = SystemMessage(content=load_txt("business_rules.txt"))
functional_requirements = SystemMessage(content=load_txt("functional_requirements.txt"))
Project_Inception_Brief = SystemMessage(content=load_txt("Project_Inception_Brief.txt"))

@st.cache_resource
def initialize_rag_components():
    """Initialize RAG components once and cache them."""
    try:
        # Initialize embeddings
        embedding_model = HuggingFaceEmbeddings(model_name="all-mpnet-base-v2")
        
        # Load knowledge base document
        knowledge_base_path = "knowledge base.docx"
        if not os.path.exists(knowledge_base_path):
            st.error(f"Knowledge base file '{knowledge_base_path}' not found!")
            return None, None
        
        # Load and process the knowledge base document
        loader = UnstructuredWordDocumentLoader(knowledge_base_path)
        docs = loader.load()
        
        # Split into chunks
        text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
        all_splits = text_splitter.split_documents(docs)
        
        # Create FAISS vector store from documents
        vector_store = FAISS.from_documents(all_splits, embedding_model)
        
        return embedding_model, vector_store
    except Exception as e:
        st.error(f"Error initializing RAG components: {e}")
        return None, None

@st.cache_resource
def load_llm():
    """Load and cache the Gemini LLM."""
    try:
        return ChatGoogleGenerativeAI(
            model="gemini-2.0-flash",
            google_api_key=st.secrets["GOOGLE_API_KEY"],
            convert_system_message_to_human=True
        )
    except Exception as e:
        st.error(f"Error loading LLM: {e}")
        return None

# Define RAG State
class State(TypedDict):
    question: str
    context: List[Document]
    answer: str
    extracted_data: Dict[str, Any]
    missing_fields: List[str]
    clarification_questions: List[str]

def setup_rag_pipeline(vector_store, llm):
    """Set up the RAG pipeline using LangGraph."""
    
    def retrieve(state: State):
        """Retrieve relevant documents from vector store."""
        if not vector_store or not state.get("question"):
            return {"context": []}
        
        try:
            retrieved_docs = vector_store.similarity_search(state["question"], k=3)
            return {"context": retrieved_docs}
        except Exception as e:
            st.error(f"Error in retrieval: {e}")
            return {"context": []}

    def generate(state: State):
        """Analyze communication dump and extract structured information."""
        if not llm or not state.get("question"):
            return {"answer": "Sorry, I couldn't process your communication dump."}
        
        try:
            # Prepare context from retrieved documents (knowledge base requirements)
            docs_content = "\n\n".join(doc.page_content for doc in state.get("context", []))

            # Create specialized prompt for communication analysis
            if docs_content:
                prompt_text = f""" {input_instruction.content} 
                Knowledge Base  (use this to understand what data is needed):
                {docs_content}

                Communication Dump to Analyze:
                {state["question"]}"""
            else:
                prompt_text = f""" {input_instruction.content} 
                Communication Dump to Analyze:
                {state["question"]}"""


            # Get response from LLM
            response = llm.invoke([HumanMessage(content=prompt_text)])
            
            # Extract structured information from response
            extracted_data = extract_structured_data(response.content)
            missing_fields = extract_missing_fields(response.content)
            clarification_questions = extract_clarification_questions(response.content)
            
            return {
                "answer": response.content,
                "extracted_data": extracted_data,
                "missing_fields": missing_fields,
                "clarification_questions": clarification_questions
            }
        except Exception as e:
            st.error(f"Error in analysis: {e}")
            return {
                "answer": "Sorry, I encountered an error while analyzing the communication dump.",
                "extracted_data": {},
                "missing_fields": [],
                "clarification_questions": []
            }

    # Build RAG graph
    try:
        graph_builder = StateGraph(State).add_sequence([retrieve, generate])
        graph_builder.add_edge(START, "retrieve")
        return graph_builder.compile()
    except Exception as e:
        st.error(f"Error building RAG pipeline: {e}")
        return None

def extract_structured_data(response_text: str) -> Dict[str, Any]:
    """Extract structured data from AI response."""
    extracted_data = {}
    
    extracted_section = response_text.split("```json")[1]
    extracted_section = extracted_section.split("```")[0]
    
    extracted_data = json.loads(extracted_section)
    
    
    return extracted_data

def extract_missing_fields(response_text: str) -> List[str]:
    """Extract missing fields from AI response."""
    missing_fields = []
    
    if "MISSING DATA:" in response_text:
        missing_section = response_text.split("MISSING DATA:")[1]
        if "QUESTIONS FOR CLARIFICATION:" in missing_section:
            missing_section = missing_section.split("QUESTIONS FOR CLARIFICATION:")[0]
        elif "NEXT STEPS:" in missing_section:
            missing_section = missing_section.split("NEXT STEPS:")[0]
        
        lines = missing_section.strip().split('\n')
        for line in lines:
            line = line.strip().strip('-').strip('*').strip()
            if line and line != "":
                missing_fields.append(line)
    
    return missing_fields

def extract_clarification_questions(response_text: str) -> List[str]:
    """Extract clarification questions from AI response."""
    questions = []
    
    if "QUESTIONS FOR CLARIFICATION:" in response_text:
        questions_section = response_text.split("QUESTIONS FOR CLARIFICATION:")[1]
        
        lines = questions_section.strip().split('\n')
        for line in lines:
            line = line.strip().strip('-').strip('*').strip()
            if line and line != "" and '?' in line:
                questions.append(line)
    
    return questions


def main():
    st.title("🤖 RAG-Powered Trackbot")
    st.markdown("Analyze communication dumps to extract structured information and generate JSON output!")
    
    # Initialize components
    embedding_model, vector_store = initialize_rag_components()
    llm = load_llm()
    create_database()
    
    if not embedding_model or not vector_store or not llm:
        st.error("Failed to initialize components. Please check your configuration and ensure 'knowledge base.docx' exists.")
        return
    
    # Initialize session state for data tracking
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "additional_features_messages" not in st.session_state:
        st.session_state.additional_features_messages = []
    if "extracted_data" not in st.session_state:
        st.session_state.extracted_data = {}
    if "missing_fields" not in st.session_state:
        st.session_state.missing_fields = []
    if "clarification_questions" not in st.session_state:
        st.session_state.clarification_questions = []
    if "current_question_index" not in st.session_state:
        st.session_state.current_question_index = 0
    if "asking_clarification" not in st.session_state:
        st.session_state.asking_clarification = False
    if "extraction_done" not in st.session_state:
        st.session_state.extraction_done = False
    if "additional_features" not in st.session_state:
        st.session_state.additional_features = False

    
    # Sidebar for information and settings
    with st.sidebar:
        st.header("📋 Information panel")
        
        # Show knowledge base status
        if embedding_model and vector_store:
            st.success("✅ Knowledge base loaded successfully!")
        else:
            st.error("❌ Failed to load knowledge base")

        


        
        # Show current extraction status
        if st.session_state.extracted_data or st.session_state.missing_fields:
            st.markdown("---")
            st.markdown("### 📊 Extraction Status")
            total_possible = len(st.session_state.extracted_data) + len(st.session_state.missing_fields)
            if total_possible > 0:
                completion = (len(st.session_state.extracted_data) / total_possible) * 100
                st.progress(completion / 100)
                st.write(f"Completion: {completion:.1f}%")
                st.write(f"Extracted: {len(st.session_state.extracted_data)} fields")
                if st.session_state.extraction_done:
                    st.write(f"Missing: 0 fields")
                else:
                    st.write(f"Missing: {len(st.session_state.missing_fields)} fields")

                    # Show missing fields
                    if st.session_state.missing_fields:
                        with st.expander("❓ Missing Fields"):
                            for field in st.session_state.missing_fields:
                                st.write(f"- {field}")
            
        
        
        # semi-hidden section for additional features
        if st.session_state.extracted_data or st.session_state.missing_fields:
            if st.button("📄 Save JSON To DB"):
                if st.session_state.extracted_data :
                     with st.spinner("Generating JSON..."):
                        prompt_text = f""" {Get_Json_prompt.content} {st.session_state.extracted_data}"""
                        response = llm.invoke([HumanMessage(content=prompt_text)])
                        response = response.content
                        response = response.replace("```json", "").replace("```", "")
                        response = json.loads(response)
                        ans = Rag_to_DB(response)
                        if isinstance(ans, bool):
                            st.success("JSON generated and saved successfully!", icon="✅")
                        else:
                            st.error(f"{ans}", icon="❌")
                            

 
            if st.button("🔄 Usable documentation"):
                st.session_state.additional_features = not st.session_state.additional_features
                                                                                                                        
        
            # Clear data button
            if st.button("🗑️ Clear All Data & Memory"):
                st.session_state.extracted_data = {}
                st.session_state.missing_fields = []
                st.session_state.clarification_questions = []
                st.session_state.current_question_index = 0
                st.session_state.asking_clarification = False
                st.session_state.messages = []

                st.success("All data and memory cleared!")
                st.rerun()
        
        st.markdown("---")
        st.markdown("### ℹ️ How it works")
        st.markdown("""
        1. Paste your communication dump in the chat
        2. AI extracts available information
        3. AI asks for missing required data
        4. Provide answers or say "skip" for null values
        5. Generate final JSON with all data
        """)

    if st.session_state.additional_features:
        for message in st.session_state.additional_features_messages:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])
            st.markdown("---")


        text = "Options to generate additional documents based on extracted data"
        st.markdown(f"<h5 style='text-align:center'>{text}</h5>", unsafe_allow_html=True)
        st.markdown("---")
        col1, col2, col3, col4, = st.columns(4)

        with col1:
            # Generate User Stories 
            if st.button("Generate User Stories"):
                with st.spinner("Generating User Stories..."):
                    prompt_text = f""" {user_stories_prompt.content} Json File with infomation: 
                    {st.session_state.extracted_data}"""
                    response = llm.invoke([HumanMessage(content=prompt_text)])
                    response = response.content
                    st.session_state.additional_features_messages.append({"role": "assistant","content": response})
                    st.rerun()
        with col2:
            # Generate business rules
            if st.button("Generate Business Rules"):
                with st.spinner("Generating Business Rules..."):
                    prompt_text = f""" {business_rules.content} Json File with infomation:
                    {st.session_state.extracted_data}"""
                    response = llm.invoke([HumanMessage(content=prompt_text)])
                    response = response.content
                    st.session_state.additional_features_messages.append({"role": "assistant","content": response})
                    st.rerun()

        with col3:
            # Generate Functional Requirements 
            if st.button("Generate Functional Requirements"):
                with st.spinner("Generating Functional Requirements..."):
                    prompt_text = f""" {functional_requirements.content} Json File with infomation:
                    {st.session_state.extracted_data}"""
                    response = llm.invoke([HumanMessage(content=prompt_text)])
                    response = response.content
                    st.session_state.additional_features_messages.append({"role": "assistant","content": response})
                    st.rerun()

        with col4:
            # Generate Project Inception Brief
            if st.button("Generate Project Inception Brief"):
                with st.spinner("Generating Project Inception Brief..."):
                    prompt_text = f""" {Project_Inception_Brief.content} Json File with infomation:
                    {st.session_state.extracted_data}"""
                    response = llm.invoke([HumanMessage(content=prompt_text)])
                    response = response.content
                    st.session_state.additional_features_messages.append({"role": "assistant","content": response})
                    st.rerun()


    else:
        # Display chat history
        for message in st.session_state.messages:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])
        
        # Handle clarification questions
        if st.session_state.asking_clarification and st.session_state.clarification_questions:
            current_idx = st.session_state.current_question_index
            if current_idx < len(st.session_state.clarification_questions):
                question = st.session_state.clarification_questions[current_idx]
                st.info(f"Question {current_idx + 1} of {len(st.session_state.clarification_questions)}: {question}")
        
        # Chat input
        input_placeholder = "Paste your communication dump here..." if not st.session_state.asking_clarification else "Provide the requested information or type 'skip' to leave as null..."
        
        prompt = st.chat_input(input_placeholder)
        if prompt:

            # Add user message to chat history
            st.session_state.messages.append({"role": "user", "content": prompt})
            
            # Display user message
            with st.chat_message("user"):
                st.markdown(prompt)
            
            # Handle clarification responses
            if st.session_state.asking_clarification and st.session_state.clarification_questions:
                current_idx = st.session_state.current_question_index
                if current_idx < len(st.session_state.clarification_questions):
                    question = st.session_state.clarification_questions[current_idx]
                    
                    # Process the answer
                    with st.chat_message("assistant"):
                        null_responses = ['skip', 'null', 'n/a', 'none', 'no']
                        normalized_prompt = prompt.strip().lower()

                        # Accept null *only if* it exactly matches or is simple negation
                        if normalized_prompt in null_responses or re.match(r"^(no|none|n/a)[\s\.,;!]*$", normalized_prompt):
                            response = f"Noted. '{question}'= can not provide any more details Use any info you have or set it to null."
                        else:
                            response = f"Thank you! I've recorded: {question} = {prompt}"
                        
                        
                        st.markdown(response)
                        st.session_state.messages.append({"role": "assistant", "content": response})
                    
                    # Move to next question
                    st.session_state.current_question_index += 1
                    
                    # Check if all questions are answered
                    if st.session_state.current_question_index >= len(st.session_state.clarification_questions):
                        st.session_state.current_question_index = 0
                        st.session_state.asking_clarification = False
                        st.session_state.clarification_questions = []
                        st.session_state.missing_fields = []

                        rag_graph = setup_rag_pipeline(vector_store, llm)

                        if rag_graph:
                            with st.chat_message("assistant"):
                                with st.spinner("Processing clarification answers..."):
                                    try:
                                        # Include chat history in the prompt
                                        chat_history = "\n".join(
                                            f"{message['role'].capitalize()}: {message['content']}" for message in st.session_state.messages
                                        )
                                        # Run RAG pipeline with clarification answers
                                        result = rag_graph.invoke({"question": chat_history})
                                        answer = result.get("answer", "Sorry, I couldn't process the clarification answers.")

                                        # Update session state with new extracted information
                                        new_extracted_data = result.get("extracted_data", {})
                                        st.session_state.extracted_data.update(new_extracted_data)

                                        # Add new missing fields (avoid duplicates)
                                        new_missing_fields = result.get("missing_fields", [])
                                        for field in new_missing_fields:
                                            if field not in st.session_state.missing_fields and field not in st.session_state.extracted_data:
                                                st.session_state.missing_fields.append(field)

                                        # Add new clarification questions (avoid duplicates)
                                        new_clarification_questions = result.get("clarification_questions", [])
                                        for question in new_clarification_questions:
                                            if question not in st.session_state.clarification_questions and question not in st.session_state.extracted_data:
                                                st.session_state.clarification_questions.append(question)

                                        # Display analysis result
                                        st.markdown(answer)

                                        # Restart clarification process if needed
                                        if st.session_state.clarification_questions:
                                            st.session_state.asking_clarification = True
                                            st.session_state.current_question_index = 0
                                            clarification_msg = f"I found some new information but need clarification on {len(st.session_state.clarification_questions)} items. I'll ask you one by one:"
                                            st.markdown(clarification_msg)
                                            st.session_state.messages.append({"role": "assistant", "content": clarification_msg})
                                            
                                        else:
                                            st.session_state.missing_fields = []
                                            st.session_state.extraction_done = True
                                            st.markdown("No further clarification needed. All data processed successfully.")
                                            st.session_state.messages.append({"role": "assistant", "content": answer})

                                    except Exception as e:
                                        error_msg = f"Error processing clarification answers: {e}"
                                        st.error(error_msg)
                                        st.session_state.messages.append({"role": "assistant", "content": error_msg})
                        else:
                            st.error("Failed to set up analysis pipeline.")

                    st.rerun()

            
            else:
                # Process communication dump
                rag_graph = setup_rag_pipeline(vector_store, llm)
                
                if rag_graph:
                    with st.chat_message("assistant"):
                        with st.spinner("Analyzing communication dump..."):
                            try:
                                # Run RAG pipeline
                                result = rag_graph.invoke({"question": prompt})
                                answer = result.get("answer", "Sorry, I couldn't analyze the communication dump.")
                                
                                # Update session state with extracted information (append, don't replace)
                                new_extracted_data = result.get("extracted_data", {})
                                st.session_state.extracted_data.update(new_extracted_data)
                                
                                # Add new missing fields (avoid duplicates)
                                new_missing_fields = result.get("missing_fields", [])
                                for field in new_missing_fields:
                                    if field not in st.session_state.missing_fields and field not in st.session_state.extracted_data:
                                        st.session_state.missing_fields.append(field)
                                
                                # Add new clarification questions (avoid duplicates)
                                new_clarification_questions = result.get("clarification_questions", [])
                                for question in new_clarification_questions:
                                    if question not in st.session_state.clarification_questions and question not in st.session_state.extracted_data:
                                        st.session_state.clarification_questions.append(question)
                                

                                # Display analysis result
                                st.markdown(answer)
                                st.session_state.messages.append({"role": "assistant", "content": answer})

                                # Start clarification process if needed
                                if st.session_state.clarification_questions and not st.session_state.asking_clarification:
                                    st.session_state.asking_clarification = True
                                    st.session_state.current_question_index = 0
                                    clarification_msg = f"I found some information but need clarification on {len(st.session_state.clarification_questions)} items. I'll ask you one by one:"
                                    st.markdown(clarification_msg)
                                    st.session_state.messages.append({"role": "assistant", "content": clarification_msg})
                                    st.rerun()

                                
                            except Exception as e:
                                error_msg = f"Error analyzing communication dump: {e}"
                                st.error(error_msg)
                                st.session_state.messages.append({
                                    "role": "assistant", 
                                    "content": error_msg
                                })
                else:
                    st.error("Failed to set up analysis pipeline.")

if __name__ == "__main__":
    main()
