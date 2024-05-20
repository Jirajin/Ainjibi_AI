import uuid
import os
import json
from dotenv import load_dotenv
import time
from typing import Dict, Optional
from pymongo import MongoClient
from pinecone import Pinecone
import streamlit as st
from langchain_community.vectorstores.pinecone import Pinecone as LangChainPinecone
from langchain_openai.embeddings import OpenAIEmbeddings
from langchain_openai.llms import OpenAI
from langchain.memory import ConversationBufferMemory
from langchain.chains import ConversationalRetrievalChain
from fastapi import HTTPException

load_dotenv()

INDEX_NAME_TEMPLATE = "langchain-doc-index-{}"
PROMPT_TEMPLATE = """
Question:
"""
NUM_RETRIEVED_DOCS = 5
TEMPERATURE = 0.3
CONVERSATION_MEMORY_SIZE = 5

CHAT_HISTORY_FILE_TEMPLATE = "chat_history_{}_{}.json"

openai_api_key = None
pinecone_index_name = None
chat_history_files = []


def get_db():
    # replace with your db uri
    uri = "<ENTER YOUR MONGODB URI HERE>"
    client = MongoClient(uri)
    db = client["<ENTER YOUR DBCLIENT HERE>"]  # replace with your database name
    return db


def generate_chat_history_file(openai_api_key: str) -> str:
    """
    This function is responsible for generating a new chat history file.
    It takes in the OpenAI API key as input and returns the newly created chat history file.
    """
    uuid_str = str(uuid.uuid4())
    return CHAT_HISTORY_FILE_TEMPLATE.format(openai_api_key, uuid_str)


def load_chat_history(chat_history_id):
    """
    This function is responsible for loading the chat history from a file.
    It takes in the chat history file as input and returns the loaded chat history.
    """
    db = get_db()
    chat_histories = db["chat_histories"]
    chat_history = chat_histories.find_one({"_id": chat_history_id})
    return chat_history["chat_history"] if chat_history else []


def save_chat_history(chat_history, chat_history_id):
    db = get_db()
    chat_histories = db["chat_histories"]
    chat_histories.update_one({"_id": chat_history_id}, {"$set": {"chat_history": chat_history}}, upsert=True)


def get_chat_history_files(openai_api_key: str):
    """
    This function is responsible for getting the chat history files.
    It takes in the OpenAI API key as input and returns the chat history files associated with that key.
    """
    db = get_db()
    chat_histories = db["chat_histories"]
    chat_history_ids = [doc["_id"] for doc in chat_histories.find()]
    return chat_history_ids


def delete_chat_history(chat_history_id: str):
    """
    This function deletes the chat history from MongoDB based on the provided chat history ID.
    """
    db = get_db()
    chat_histories = db["chat_histories"]
    result = chat_histories.delete_one({"_id": chat_history_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Chat history not found")
    else:
        return {"message": "Chat history deleted successfully"}


def initialize_pinecone_client(api_key: str) -> Pinecone:
    return Pinecone(api_key=st.secrets["pinecone_api_key"])


def get_response(user_query: str, chat_history: list, pinecone_index_number: str) -> Dict[str, Optional[str]]:
    """
    This function is responsible for getting the response from the AI model.
    It takes in the user's query, the chat history, and the Pinecone index number as input.
    It initializes the language model, the retriever, and the conversational retrieval chain.
    It then uses these to get a response from the AI model and appends the response to the chat history.
    If an error occurs during this process, it returns an error message.
    """

    try:
        full_query = PROMPT_TEMPLATE + user_query

        if not pinecone_index_number:
            return {"result": "Pinecone index number is not set", "chat_history": chat_history}

        # Construct Pinecone index name using the incoming Pinecone index number
        index_name = INDEX_NAME_TEMPLATE.format(pinecone_index_number)

        pc_client = Pinecone(api_key=st.secrets["pinecone_api_key"])
        embedding_model = OpenAIEmbeddings(model="text-embedding-3-large", openai_api_key=openai_api_key)
        index = pc_client.Index(index_name)  # Use constructed index name

        time.sleep(1)

        index.describe_index_stats()
        conversation_memory = ConversationBufferMemory(memory_key="chat_history", return_messages=True)

        vectorstore = LangChainPinecone(index=index, embedding=embedding_model, text_key="context")
        llm = OpenAI(temperature=TEMPERATURE, openai_api_key=openai_api_key)
        retriever = vectorstore.as_retriever(search_type="similarity", search_kwargs={"k": NUM_RETRIEVED_DOCS})

        qa_chain = ConversationalRetrievalChain.from_llm(llm=llm,
                                                         retriever=retriever,
                                                         memory=conversation_memory)

        result = qa_chain({'question': full_query, 'chat_history': chat_history})

        response = result['answer']
        chat_history.append((full_query, response))

        return {'result': result, 'chat_history': chat_history}
    except Exception as e:
        print(f"An error occurred: {e}")
        return {"answer": "An error occurred while processing your request. Please try again later.", "sources": None,
                "chat_history": chat_history}


st.title("Conversational AI")

openai_api_key_input = st.text_input("Enter your OpenAI API key:")

if openai_api_key_input:
    openai_api_key = openai_api_key_input
    pinecone_index_number = st.text_input("Enter the Pinecone index number:")
    if pinecone_index_number:
        pinecone_index_name = INDEX_NAME_TEMPLATE.format(pinecone_index_number)
        chat_history_files = get_chat_history_files(openai_api_key)

        if not chat_history_files:
            st.write("No chat history found. Create a new chat.")
        else:
            st.write("Chat Histories:")
            for i, chat_history_file in enumerate(chat_history_files):
                st.write(f"Chat History {i + 1}: {chat_history_file}")

        if st.button("Create New Chat"):
            chat_history_file = generate_chat_history_file(openai_api_key)
            chat_history_files.append(chat_history_file)
            chat_history = []
            save_chat_history(chat_history, chat_history_file)
            st.write("New chat created successfully!")

        if chat_history_files:
            selected_chat_history = st.selectbox("Select a chat history:", chat_history_files)

            if selected_chat_history:
                chat_history_file = selected_chat_history
                chat_history = load_chat_history(chat_history_file)

                user_query = st.text_area("Enter your question here:")

                if st.button("Get Response"):
                    response = get_response(user_query, chat_history)
                    st.write("Answer:", response['result'].get("answer", "No answer found."))
                    sources = response['result'].get("sources")
                    if sources:
                        st.write("Source:", os.path.basename(sources))
                    else:
                        st.write("No specific source cited.")

                    st.write("Chat History:")
                    for i, (question, answer) in enumerate(response['chat_history']):
                        st.write(f"Turn {i + 1}:")
                        st.write(f"Q: {question}")
                        st.write(f"A: {answer}")
                        st.write("---")

                    # Save updated chat history
                    save_chat_history(response['chat_history'], chat_history_file)
    else:
        st.error("Please enter a Pinecone index number.")
else:
    st.error("Please enter an OpenAI API key.")
