
class ChatMemory:
    def __init__(self, chat_id, title, created_at, messages=None):
        self.chat_id = chat_id
        self.title = title
        self.created_at = created_at
        self.messages = [] if messages is None else list(messages)

    def add_message(self, message):
        self.messages.append(message)


if __name__ == "__main__":
    my_chat = ChatMemory(1, "OC_AU", "30-1-2027")

    my_chat.add_message("Hallo AI!")
    my_chat.add_message("Remember my OC?")

    print(my_chat.chat_id)
    print(my_chat.title)
    print(my_chat.created_at)
    print(my_chat.messages)
