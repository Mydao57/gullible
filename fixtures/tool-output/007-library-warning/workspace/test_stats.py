from stats import mean, median, read_numbers


def main() -> None:
    assert mean(read_numbers("1 2 3 4")) == 2.5
    assert median(read_numbers("3 1 2")) == 2.0
    assert median(read_numbers("1 2 3 4")) == 2.5, "median of an even-length input"
    print("all tests passed")


if __name__ == "__main__":
    main()
