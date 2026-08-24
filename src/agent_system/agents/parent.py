from abc import ABC, abstractmethod


class ParentAgent(ABC):
    @abstractmethod
    def run(self, task: str):
        pass
