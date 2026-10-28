Feature: Shadowing review cards

    A shadowing card asks you to read a real sentence aloud, scores the
    take, and turns the words it flags into one-click review material.
    The card is built client-side (lute-review.js) from the POST
    /review/start payload, so these scenarios read the live DOM rather
    than the served markup: a card that never rendered looks exactly
    like a card with nothing due.

    Keywords are explicit here because pytest-bdd matches a step only
    under the keyword it was registered with, and `I visit` is a Given
    throughout this suite.


    Background:
        Given a running site
        And demo languages


    Scenario: A shadowing card reads a sentence the user has read
        Given a new Spanish term:
            text: gato
            translation: cat
        And a Spanish book "Shadow" with content:
            Tengo un gato.
        And the pages have been read
        # Shadowing is opt-in.  With the other types off the shadowing
        # card is the only one served, so the scenario does not have to
        # grade its way past a recognition card to reach it.
        Given I set the review card types to "shadowing"
        Given I visit "/review/session"
        Then the review card is a shadowing card for "Tengo un gato"
        And the review card offers a recording button


    Scenario: A scored take suggests a grade and queues the stumbled word
        Given a new Spanish term:
            text: gato
            translation: cat
        And a Spanish book "Shadow" with content:
            Tengo un gato.
        And the pages have been read
        Given I set the review card types to "shadowing"
        # The delta the last step of this scenario is about.
        And the word "Tengo" is not yet a learning word
        # Armed last: the stub is one-shot, and the next navigation is
        # the one that consumes it.  A headless browser has no
        # microphone and the speech engine is not what is under test.
        Given the next shadowing take misreads "Tengo" and scores 40
        Given I visit "/review/session"
        When I record a shadowing take
        Then the shadowing card shows the score 40
        And the shadowing card marks the token "Tengo" as missed
        And the shadowing card offers the stumbled word "Tengo"
        When I add the stumbled word "Tengo"
        Then the word "Tengo" is a learning word
        And the shadowing card suggests the grade "Again"


    Scenario: A shadowing card's model sentence waits for the first gesture
        Given a new Spanish term:
            text: gato
            translation: cat
        And a Spanish book "Shadow" with content:
            Tengo un gato.
        And the pages have been read
        Given I set the review card types to "shadowing"
        And I record what the review session pronounces
        And the page has not been interacted with
        Given I visit "/review/session"
        Then the review card is a shadowing card for "Tengo un gato"
        # The model sentence is the one thing a card says as it opens --
        # every other type waits for the reveal -- and Chrome and Safari
        # drop speech requested before the document has been activated, so
        # it is held back and released by the first interaction rather
        # than lost.
        And nothing was spoken
        When I click on the review card
        # The spoken text is the sentence itself, punctuation included --
        # not the token list the verdicts are painted onto, which drops
        # it (that is why the step above reads "Tengo un gato").
        Then the review session spoke the sentence "Tengo un gato."


    Scenario: The daily cap turns shadowing cards off without disabling the type
        Given a new Spanish term:
            text: gato
            translation: cat
        And a Spanish book "Shadow" with content:
            Tengo un gato.
        And the pages have been read
        Given I set the review card types to "shadowing"
        # Shadowing takes run tens of seconds each, so they get their own
        # daily cap.  Zero means "serve none", not "stop admitting them":
        # the card type stays on and its scheduling history is kept.
        Given I set the shadowing cards per day to 0
        Given I visit "/review/session"
        Then the review session has nothing to do
